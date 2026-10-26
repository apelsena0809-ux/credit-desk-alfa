"""Слой данных: SQLite-хранилище заявок, аудита, документов, решений и выдач.

Данные полностью синтетические. Жизненный цикл заявки: подача → проверки →
решение → принятие предложения клиентом → подписание (демо-код) → выдача.
Переходы статусов валидируются по карте `rules.ALLOWED_TRANSITIONS`.
"""
from __future__ import annotations

import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rules import ALLOWED_TRANSITIONS, OFFER_STATUSES

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
UPLOAD_DIR = DATA_DIR / 'uploads'
DB_PATH = DATA_DIR / 'credit_desk.db'

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
# Срок жизни и длина демо-кода подписания (имитация SMS/пуша)
SIGNING_CODE_TTL_MINUTES = 15
SIGNING_CODE_LENGTH = 4

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id      TEXT NOT NULL UNIQUE,
    company_name        TEXT NOT NULL,
    inn                 TEXT NOT NULL,
    product             TEXT NOT NULL,
    amount              REAL NOT NULL,
    term_months         INTEGER NOT NULL,
    purpose             TEXT NOT NULL,
    annual_revenue      REAL NOT NULL DEFAULT 0,
    concentration_pct   INTEGER NOT NULL DEFAULT 0,
    period_mismatch     INTEGER NOT NULL DEFAULT 0,
    missing_bki         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT','SUBMITTED','VALIDATION','ENRICHMENT','SCORING',
                                          'MANUAL_REVIEW','NEEDS_INFO','APPROVED','CONDITIONAL_APPROVAL',
                                          'REJECTED','ACCEPTED','SIGNING','SIGNED','DISBURSED')),
    client_message      TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_fk  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    timestamp       TEXT NOT NULL,
    actor_role      TEXT NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    from_status     TEXT,
    to_status       TEXT,
    reason          TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_fk  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    stored_name     TEXT,
    content_type    TEXT,
    size_bytes      INTEGER DEFAULT 0,
    uploaded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_fk  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    decision_type   TEXT NOT NULL,
    terms           TEXT,
    reason          TEXT,
    actor           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS disbursements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_fk  INTEGER NOT NULL UNIQUE REFERENCES applications(id) ON DELETE CASCADE,
    account_number  TEXT NOT NULL,
    amount          REAL NOT NULL,
    terms           TEXT,
    disbursed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signing_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_fk  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    code            TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    used_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_audit_app ON audit(application_fk);
CREATE INDEX IF NOT EXISTS idx_docs_app ON documents(application_fk);
CREATE INDEX IF NOT EXISTS idx_dec_app ON decisions(application_fk);
"""


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _safe_name(filename: str) -> str:
    cleaned = re.sub(r'[^A-Za-zА-Яа-я0-9._-]+', '_', filename or 'file')
    return cleaned[:120] or 'file'


@contextmanager
def get_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Создаёт схему и наполняет базу демонстрационными заявками при первом запуске."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        count = conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0]
        if count == 0:
            _seed_demo(conn)


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _next_application_id(conn: sqlite3.Connection) -> str:
    prefix = f'ALF-{datetime.now():%y}-'
    seq = conn.execute("SELECT COUNT(*) FROM applications WHERE application_id LIKE ?", (prefix + '%',)).fetchone()[0] + 1
    while conn.execute('SELECT 1 FROM applications WHERE application_id = ?', (f'{prefix}{seq:04d}',)).fetchone():
        seq += 1
    return f'{prefix}{seq:04d}'


def list_applications() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM applications ORDER BY created_at DESC, id DESC').fetchall()
        return [dict(r) for r in rows]


def get_application(ref: Any) -> dict | None:
    """Принимает внутренний id (int) или публичный номер заявки (str)."""
    if ref is None:
        return None
    with get_conn() as conn:
        row = None
        try:
            row = conn.execute('SELECT * FROM applications WHERE id = ?', (int(ref),)).fetchone()
        except (TypeError, ValueError):
            row = None
        if row is None:
            row = conn.execute('SELECT * FROM applications WHERE application_id = ?', (str(ref),)).fetchone()
        return _row(row)


def create_application(company_name, inn, product, amount, term_months, purpose,
                       annual_revenue, concentration_pct, period_mismatch, missing_bki) -> int:
    stamp = _now()
    with get_conn() as conn:
        public_id = _next_application_id(conn)
        cursor = conn.execute(
            """INSERT INTO applications
               (application_id, company_name, inn, product, amount, term_months, purpose,
                annual_revenue, concentration_pct, period_mismatch, missing_bki, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (public_id, company_name, inn, product, float(amount), int(term_months), purpose,
             float(annual_revenue), int(concentration_pct), int(bool(period_mismatch)),
             int(bool(missing_bki)), 'DRAFT', stamp, stamp),
        )
        return int(cursor.lastrowid)


def update_status(pk: int, new_status: str, actor: str = 'system', reason: str = '',
                  client_message: str | None = None, log: bool = True) -> None:
    """Меняет статус заявки с проверкой перехода и записью в журнал аудита."""
    with get_conn() as conn:
        row = conn.execute('SELECT status FROM applications WHERE id = ?', (int(pk),)).fetchone()
        if row is None:
            raise ValueError(f'Заявка {pk} не найдена')
        previous = row['status']
        if new_status != previous and not can_transition(previous, new_status):
            raise ValueError(
                f'Переход {previous} → {new_status} не разрешён жизненным циклом заявки. '
                f'Допустимо: {", ".join(ALLOWED_TRANSITIONS.get(previous, ())) or "нет переходов"}')
        conn.execute(
            'UPDATE applications SET status = ?, updated_at = ?, client_message = COALESCE(?, client_message) WHERE id = ?',
            (new_status, _now(), client_message, int(pk)),
        )
        if log and previous != new_status:
            conn.execute(
                '''INSERT INTO audit (application_fk, timestamp, actor_role, actor, action, from_status, to_status, reason)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (int(pk), _now(), 'bank' if actor in ('analyst', 'senior_analyst', 'admin', 'system') else 'client',
                 actor, 'STATUS_CHANGE', previous, new_status, reason or None),
            )


def can_transition(current: str, new_status: str) -> bool:
    return new_status in ALLOWED_TRANSITIONS.get(current, ())


def add_audit(pk: int, actor_role: str, actor: str, action: str,
              from_status: str | None = None, to_status: str | None = None, reason: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            '''INSERT INTO audit (application_fk, timestamp, actor_role, actor, action, from_status, to_status, reason)
               VALUES (?,?,?,?,?,?,?,?)''',
            (int(pk), _now(), actor_role, actor, action, from_status, to_status, reason),
        )


def get_audit(pk: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM audit WHERE application_fk = ? ORDER BY timestamp DESC, id DESC', (int(pk),)
        ).fetchall()
        return [dict(r) for r in rows]


def save_document(pk: int, filename: str, content: bytes, content_type: str) -> bool:
    """Сохраняет файл на диск и запись в БД. Возвращает False, если файл слишком большой."""
    if content is None or len(content) > MAX_UPLOAD_BYTES:
        return False
    stamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
    stored_name = f"{int(pk)}_{stamp}_{{name}}".format(name=_safe_name(filename))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / stored_name).write_bytes(content)
    with get_conn() as conn:
        conn.execute(
            '''INSERT INTO documents (application_fk, filename, stored_name, content_type, size_bytes, uploaded_at)
               VALUES (?,?,?,?,?,?)''',
            (int(pk), filename, stored_name, content_type or 'application/octet-stream', len(content), _now()),
        )
    return True


def get_documents(pk: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT * FROM documents WHERE application_fk = ? ORDER BY uploaded_at DESC', (int(pk),)
        ).fetchall()
        return [dict(r) for r in rows]


def set_decision(pk: int, decision_type: str, terms: str, reason: str, actor: str) -> None:
    with get_conn() as conn:
        conn.execute(
            '''INSERT INTO decisions (application_fk, decision_type, terms, reason, actor, created_at)
               VALUES (?,?,?,?,?,?)''',
            (int(pk), decision_type, terms, reason, actor, _now()),
        )


def get_decision(pk: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM decisions WHERE application_fk = ? ORDER BY id DESC LIMIT 1', (int(pk),)
        ).fetchone()
        return _row(row)


def accept_offer(pk: int) -> None:
    """Клиент принимает кредитное предложение — переход в ACCEPTED."""
    update_status(pk, 'ACCEPTED', actor='client',
                  reason='Клиент принял кредитное предложение в личном кабинете')
    add_audit(pk, 'client', 'client', 'OFFER_ACCEPTED', 'ACCEPTED', None,
              'Согласие на условия зафиксировано')


def decline_offer(pk: int) -> None:
    """Клиент отказывается от одобренного предложения — заявка закрывается отказом клиента."""
    update_status(pk, 'REJECTED', actor='client',
                  reason='Клиент отказался от одобренного предложения')
    add_audit(pk, 'client', 'client', 'OFFER_DECLINED', 'REJECTED', None,
              'Предложение не принято клиентом')


def create_signing_code(pk: int) -> str:
    """Создаёт демо-код подписания и переводит заявку в SIGNING.

    В имитации SMS код возвращается клиенту в интерфейсе.
    """
    code = ''.join(secrets.choice('0123456789') for _ in range(SIGNING_CODE_LENGTH))
    expires = (datetime.now() + timedelta(minutes=SIGNING_CODE_TTL_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    with get_conn() as conn:
        conn.execute('UPDATE signing_codes SET used_at = ? WHERE application_fk = ? AND used_at IS NULL',
                     (_now(), int(pk)))
        conn.execute(
            'INSERT INTO signing_codes (application_fk, code, expires_at) VALUES (?,?,?)',
            (int(pk), code, expires),
        )
    update_status(pk, 'SIGNING', actor='client', reason='Выдан демо-код подписания (имитация SMS)')
    add_audit(pk, 'system', 'system', 'SIGNING_CODE_ISSUED', None,
              f'Демо-код подписания действителен {SIGNING_CODE_TTL_MINUTES} минут (имитация SMS)')
    return code


def verify_signing_code(pk: int, code: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM signing_codes WHERE application_fk = ? AND used_at IS NULL ORDER BY id DESC LIMIT 1',
            (int(pk),),
        ).fetchone()
    if row is None or row['code'] != str(code).strip():
        return False
    if row['expires_at'] < _now():
        return False
    with get_conn() as conn:
        conn.execute('UPDATE signing_codes SET used_at = ? WHERE id = ?', (_now(), row['id']))
    return True


def get_active_signing_code(pk: int) -> dict | None:
    """Текущий неиспользованный и неистёкший демо-код подписания.

    В реальном продукте код приходит по SMS/пушу; здесь он показывается
    в интерфейсе, потому что доставка сообщений смоделирована.
    """
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM signing_codes WHERE application_fk = ? AND used_at IS NULL '
            'ORDER BY id DESC LIMIT 1', (int(pk),),
        ).fetchone()
    if row is None or row['expires_at'] < _now():
        return None
    return _row(row)


def sign_application(pk: int, code: str) -> bool:
    """Проверяет демо-код и переводит заявку в SIGNED (имитация ЭП)."""
    if not verify_signing_code(pk, code):
        return False
    update_status(pk, 'SIGNED', actor='client',
                  reason='Кредитное предложение подписано демо-кодом (имитация ЭП)')
    add_audit(pk, 'client', 'client', 'APPLICATION_SIGNED', 'SIGNED', None,
              'Подписание смоделировано внутри продукта; реальная ЭП не используется')
    return True


def disburse(pk: int) -> dict:
    """Смоделированная выдача кредита: запись о зачислении на демо-счёт."""
    app = get_application(pk)
    if app is None:
        raise ValueError('Заявка не найдена')
    existing = get_disbursement(pk)
    if existing:
        return existing
    account = f'40802810{int(pk):010d}'
    terms = ''
    decision = get_decision(pk)
    if decision:
        terms = decision.get('terms') or ''
    with get_conn() as conn:
        conn.execute(
            'INSERT INTO disbursements (application_fk, account_number, amount, terms, disbursed_at) VALUES (?,?,?,?,?)',
            (int(pk), account, float(app['amount']), terms, _now()),
        )
    update_status(pk, 'DISBURSED', actor='system',
                  reason='Смоделированная выдача: зачисление на демо-счёт (реальные проводки не выполняются)')
    add_audit(pk, 'system', 'system', 'LOAN_DISBURSED', 'DISBURSED', None,
              f'Смоделированная выдача {app["amount"]:,.0f} ₽ на демо-счёт {account}'.replace(',', ' '))
    return get_disbursement(pk)


def get_disbursement(pk: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            'SELECT * FROM disbursements WHERE application_fk = ? ORDER BY id DESC LIMIT 1', (int(pk),)
        ).fetchone()
        return _row(row)


def inn_has_active_application(inn: str) -> dict | None:
    """Ищет активную (не завершённую) заявку по ИНН — для предупреждения о дубле."""
    active = ('SUBMITTED', 'VALIDATION', 'ENRICHMENT', 'SCORING', 'MANUAL_REVIEW', 'NEEDS_INFO',
              'APPROVED', 'CONDITIONAL_APPROVAL', 'ACCEPTED', 'SIGNING', 'SIGNED')
    placeholders = ','.join('?' * len(active))
    with get_conn() as conn:
        row = conn.execute(
            f'SELECT * FROM applications WHERE inn = ? AND status IN ({placeholders}) ORDER BY id DESC LIMIT 1',
            (str(inn).strip(), *active),
        ).fetchone()
        return _row(row)


def status_counts() -> dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute('SELECT status, COUNT(*) AS c FROM applications GROUP BY status').fetchall()
        return {r['status']: r['c'] for r in rows}


def reset_demo_data() -> None:
    """Полностью очищает базу и загруженные файлы, затем заново наполняет демо-данными."""
    with get_conn() as conn:
        conn.executescript(
            'DELETE FROM signing_codes; DELETE FROM disbursements; DELETE FROM decisions; '
            'DELETE FROM documents; DELETE FROM audit; DELETE FROM applications;')
        _seed_demo(conn)
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR, ignore_errors=True)


def _seed_demo(conn: sqlite3.Connection) -> None:
    """Пять синтетических заявок: каждая ветка сценария и каждое исходное состояние."""
    now = datetime.now()

    def stamp(days_ago: int, hours: int = 0) -> str:
        return (now - timedelta(days=days_ago, hours=hours)).strftime('%Y-%m-%d %H:%M:%S')

    demo = [
        dict(
            company='ООО «Северный склад»', inn='7701234567', product='Оборотное финансирование',
            amount=2_400_000, term=18, purpose='Пополнение оборотных средств',
            revenue=18_000_000, concentration=62, mismatch=1, bki=0,
            status='MANUAL_REVIEW', created=9,
            events=[(9, 'Клиент', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом'),
                    (9, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'SUBMITTED', 'VALIDATION', 'Начата формальная проверка'),
                    (9, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'VALIDATION', 'ENRICHMENT', 'Запрошены демо-источники'),
                    (8, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'ENRICHMENT', 'SCORING', 'Демо-источники получены; расчёт показателей'),
                    (8, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'SCORING', 'MANUAL_REVIEW',
                     'Концентрация 62% и несопоставимые периоды — нужна экспертная проверка')],
        ),
        dict(
            company='ООО «Тёплый хлеб»', inn='7812345678', product='Краткосрочное финансирование',
            amount=900_000, term=12, purpose='Закупка товара к сезону',
            revenue=7_200_000, concentration=28, mismatch=0, bki=1,
            status='NEEDS_INFO', created=5,
            events=[(5, 'Клиент', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом'),
                    (5, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'SUBMITTED', 'NEEDS_INFO',
                     'В демо-ответе БКИ недостаточно данных')],
            client_message='Пожалуйста, приложите справку об отсутствии задолженности или пояснение по кредитной истории.',
        ),
        dict(
            company='ИП Соколов А. В.', inn='502345678901', product='Кредит на развитие бизнеса',
            amount=1_200_000, term=24, purpose='Покупка оборудования',
            revenue=9_600_000, concentration=34, mismatch=0, bki=0,
            status='SUBMITTED', created=2,
            events=[(2, 'Клиент', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом')],
        ),
        dict(
            company='ООО «ТехноПарк-Юг»', inn='6153456789', product='Оборотное финансирование',
            amount=5_000_000, term=36, purpose='Закрытие кассового разрыва',
            revenue=64_000_000, concentration=22, mismatch=0, bki=0,
            status='CONDITIONAL_APPROVAL', created=17,
            events=[(17, 'Клиент', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом'),
                    (16, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'SUBMITTED', 'VALIDATION',
                     'Начата формальная проверка'),
                    (16, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'VALIDATION', 'ENRICHMENT',
                     'Запрошены демо-источники'),
                    (16, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'ENRICHMENT', 'SCORING',
                     'Демо-источники получены'),
                    (15, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'SCORING', 'MANUAL_REVIEW',
                     'Проверка финансового профиля'),
                    (15, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'MANUAL_REVIEW', 'CONDITIONAL_APPROVAL',
                     'Встречное предложение: снижение суммы на 10% из-за сезонности поступлений')],
            decision=('CONDITIONAL_APPROVAL', 'Сумма 4 500 000 ₽, срок 36 мес., ставка 16,9%',
                      'Встречное предложение: сумма снижена на 10% из-за сезонности поступлений; '
                      'клиент может принять его в личном кабинете', 'senior_analyst', stamp(15)),
        ),
        dict(
            company='ООО «Марена-Логистик»', inn='5261234567', product='Кредит на развитие бизнеса',
            amount=1_800_000, term=24, purpose='Покупка оборудования',
            revenue=22_000_000, concentration=30, mismatch=0, bki=0,
            status='DISBURSED', created=30,
            events=[(30, 'Клиент', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом'),
                    (29, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'SUBMITTED', 'VALIDATION', 'Начата формальная проверка'),
                    (29, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'VALIDATION', 'ENRICHMENT', 'Запрошены демо-источники'),
                    (29, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'ENRICHMENT', 'SCORING', 'Демо-источники получены'),
                    (28, 'Аналитик', 'analyst', 'STATUS_CHANGE', 'SCORING', 'MANUAL_REVIEW', 'Показатели рассчитаны'),
                    (27, 'Старший аналитик', 'senior_analyst', 'STATUS_CHANGE', 'MANUAL_REVIEW', 'APPROVED',
                     'Одобрено на заявленных условиях'),
                    (27, 'Клиент', 'client', 'OFFER_ACCEPTED', 'APPROVED', 'ACCEPTED', 'Клиент принял предложение'),
                    (27, 'Клиент', 'client', 'STATUS_CHANGE', 'ACCEPTED', 'SIGNING', 'Запущено подписание'),
                    (27, 'Клиент', 'client', 'APPLICATION_SIGNED', 'SIGNING', 'SIGNED', 'Код подписания введён верно'),
                    (26, 'Система', 'system', 'LOAN_DISBURSED', 'SIGNED', 'DISBURSED',
                     'Смоделированная выдача на демо-счёт')],
            decision=('APPROVED', 'Сумма 1 800 000 ₽, срок 24 мес., ставка 16,9%',
                      'Показатели в норме, одобрение на заявленных условиях', 'senior_analyst', stamp(27)),
        ),
    ]

    for item in demo:
        created = stamp(item['created'])
        public_id = _next_application_id(conn)
        cur = conn.execute(
            """INSERT INTO applications
               (application_id, company_name, inn, product, amount, term_months, purpose, annual_revenue,
                concentration_pct, period_mismatch, missing_bki, status, client_message, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (public_id, item['company'], item['inn'], item['product'], item['amount'], item['term'],
             item['purpose'], item['revenue'], item['concentration'], item['mismatch'], item['bki'],
             item['status'], item.get('client_message'), created, created),
        )
        pk = int(cur.lastrowid)
        for days_ago, actor, role, action, src, dst, reason in item['events']:
            conn.execute(
                '''INSERT INTO audit (application_fk, timestamp, actor_role, actor, action, from_status, to_status, reason)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (pk, stamp(days_ago), role, actor, action, src, dst, reason),
            )
        if item.get('decision'):
            dtype, terms, reason, actor, created_at = item['decision']
            conn.execute(
                '''INSERT INTO decisions (application_fk, decision_type, terms, reason, actor, created_at)
                   VALUES (?,?,?,?,?,?)''',
                (pk, dtype, terms, reason, actor, created_at),
            )
        if item['status'] == 'DISBURSED':
            account = f'40802810{pk:010d}'
            conn.execute(
                'INSERT INTO disbursements (application_fk, account_number, amount, terms, disbursed_at) VALUES (?,?,?,?,?)',
                (pk, account, item['amount'], item['decision'][1], stamp(26)),
            )
