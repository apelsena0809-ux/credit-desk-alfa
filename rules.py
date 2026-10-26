"""Бизнес-правила демо-проверки заявки.

Файл отсутствовал в исходной поставке, хотя app.py импортирует из него
`evaluate_application`, `STATUS_LABELS` и `STATUS_OPTIONS`. Правила намеренно
отделены от ИИ-модуля и от расчётов: они лишь подсвечивают сигналы, решение
принимает сотрудник.
"""
from __future__ import annotations

import re

from finance import PRODUCT_LIMITS, metrics as fin_metrics

# Порядок соответствует логике жизненного цикла заявки
STATUS_LABELS: dict[str, str] = {
    'DRAFT': 'Черновик',
    'SUBMITTED': 'Отправлена',
    'VALIDATION': 'Формальная проверка',
    'ENRICHMENT': 'Сбор данных',
    'SCORING': 'Скоринг',
    'MANUAL_REVIEW': 'На ручном разборе',
    'NEEDS_INFO': 'Нужны уточнения',
    'APPROVED': 'Одобрена',
    'CONDITIONAL_APPROVAL': 'Одобрена с условиями',
    'REJECTED': 'Отклонена',
    'ACCEPTED': 'Предложение принято клиентом',
    'SIGNING': 'Подписание',
    'SIGNED': 'Подписана',
    'DISBURSED': 'Выдана',
}

STATUS_OPTIONS: list[str] = list(STATUS_LABELS.keys())

STATUS_TONE: dict[str, str] = {
    'DRAFT': 'neutral',
    'SUBMITTED': 'info',
    'VALIDATION': 'info',
    'ENRICHMENT': 'info',
    'SCORING': 'info',
    'MANUAL_REVIEW': 'warn',
    'NEEDS_INFO': 'warn',
    'APPROVED': 'ok',
    'CONDITIONAL_APPROVAL': 'ok',
    'REJECTED': 'risk',
    'ACCEPTED': 'ok',
    'SIGNING': 'alfa',
    'SIGNED': 'alfa',
    'DISBURSED': 'ok',
}

# Разрешённые переходы статусов — единый источник истины для UI и API.
# Заявка живёт от подачи до смоделированной выдачи; отказ — терминальный статус,
# после которого клиент может подать новую заявку.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    'DRAFT': ('SUBMITTED',),
    'SUBMITTED': ('VALIDATION', 'REJECTED'),
    'VALIDATION': ('ENRICHMENT', 'NEEDS_INFO', 'REJECTED'),
    'ENRICHMENT': ('SCORING', 'NEEDS_INFO'),
    'SCORING': ('MANUAL_REVIEW', 'NEEDS_INFO'),
    'MANUAL_REVIEW': ('APPROVED', 'CONDITIONAL_APPROVAL', 'REJECTED', 'NEEDS_INFO'),
    'NEEDS_INFO': ('ENRICHMENT', 'REJECTED'),
    'APPROVED': ('ACCEPTED', 'REJECTED'),
    'CONDITIONAL_APPROVAL': ('ACCEPTED', 'REJECTED'),
    'ACCEPTED': ('SIGNING',),
    'SIGNING': ('SIGNED',),
    'SIGNED': ('DISBURSED',),
    'REJECTED': (),
    'DISBURSED': (),
}

# Статусы после положительного решения — клиент может принять предложение
OFFER_STATUSES = ('APPROVED', 'CONDITIONAL_APPROVAL')
# Терминальные статусы жизненного цикла
TERMINAL_STATUSES = ('REJECTED', 'DISBURSED')

# Статусы, в которых заявка считается активной
ACTIVE_STATUSES = (
    'SUBMITTED', 'VALIDATION', 'ENRICHMENT', 'SCORING', 'MANUAL_REVIEW', 'NEEDS_INFO',
    'APPROVED', 'CONDITIONAL_APPROVAL', 'ACCEPTED', 'SIGNING', 'SIGNED',
)

SEVERITY_LABELS = {'critical': 'Блокирующий сигнал', 'warning': 'Требует внимания', 'ok': 'Без замечаний'}


def _inn_is_valid(inn: str) -> bool:
    digits = re.sub(r'\D', '', str(inn or ''))
    return len(digits) in (10, 12)


def evaluate_application(app: dict) -> list[dict]:
    """Возвращает список правил: {rule_id, title, severity, explanation}."""
    m = fin_metrics(app)
    results: list[dict] = []

    def add(rule_id: str, title: str, severity: str, explanation: str) -> None:
        results.append({'rule_id': rule_id, 'title': title, 'severity': severity, 'explanation': explanation})

    # R-01 Полнота и корректность обязательных полей
    missing = [
        label for field, label in (
            ('company_name', 'название компании'), ('inn', 'ИНН'),
            ('product', 'продукт'), ('purpose', 'цель финансирования'),
        ) if not str(app.get(field) or '').strip()
    ]
    if missing:
        add('R-01 · Полнота данных', 'Не заполнены обязательные поля',
            'critical', 'Отсутствуют: ' + ', '.join(missing) + '. Проверить заявку нельзя.')
    elif not _inn_is_valid(app.get('inn')):
        add('R-01 · Полнота данных', 'ИНН не соответствует формату',
            'warning', 'ИНН должен содержать 10 цифр (юрлицо) или 12 цифр (ИП). Текущее значение: '
            f"{app.get('inn')}.")
    else:
        add('R-01 · Полнота данных', 'Обязательные поля заполнены',
            'ok', f"Заявка {app.get('application_id', '—')} содержит все обязательные реквизиты.")

    # R-02 Долговая нагрузка
    if m['revenue'] <= 0:
        add('R-02 · Долговая нагрузка', 'Выручка не указана',
            'critical', 'Без годовой выручки невозможно оценить способность обслуживать долг.')
    elif m['dti'] > 0.35:
        add('R-02 · Долговая нагрузка', 'Высокая долговая нагрузка',
            'critical', f"Обслуживание долга — {m['dti']:.0%} годовой выручки при платеже "
            f"{m['payment']:,.0f} ₽/мес. Демонстрационный порог — 35%.".replace(',', ' '))
    elif m['dti'] > 0.25:
        add('R-02 · Долговая нагрузка', 'Нагрузка выше комфортной',
            'warning', f"Обслуживание долга — {m['dti']:.0%} годовой выручки. "
            f"Демонстрационный ориентир — не более 25%.".replace(',', ' '))
    else:
        add('R-02 · Долговая нагрузка', 'Нагрузка в допустимых границах',
            'ok', f"Обслуживание долга — {m['dti']:.0%} годовой выручки "
            f"({m['payment']:,.0f} ₽/мес.).".replace(',', ' '))

    # R-03 Концентрация выручки
    concentration = int(app.get('concentration_pct') or 0)
    if concentration >= 50:
        add('R-03 · Концентрация выручки', 'Зависимость от одного контрагента',
            'critical', f"На крупнейшего контрагента приходится {concentration}% поступлений. "
            'Нужны договорная база и оценка устойчивости отношений.')
    elif concentration >= 35:
        add('R-03 · Концентрация выручки', 'Повышенная концентрация',
            'warning', f"Доля крупнейшего контрагента — {concentration}%. "
            'Рекомендуется расшифровка поступлений по контрагентам.')
    else:
        add('R-03 · Концентрация выручки', 'Структура выручки диверсифицирована',
            'ok', f"Доля крупнейшего контрагента — {concentration}%, ниже демонстрационного порога 35%.")

    # R-04 Сопоставимость отчётных периодов
    if app.get('period_mismatch'):
        add('R-04 · Сопоставимость периодов', 'Несопоставимые отчётные периоды',
            'critical', 'Годовое значение нельзя напрямую сравнивать с квартальным. '
            'Запросить показатели за единый период и подтверждающие документы.')
    else:
        add('R-04 · Сопоставимость периодов', 'Периоды сопоставимы',
            'ok', 'Флаг несопоставимости не установлен; периоды принимаются для демонстрационного расчёта.')

    # R-05 Данные БКИ
    if app.get('missing_bki'):
        add('R-05 · Кредитная история', 'Недостаточно данных БКИ',
            'critical', 'В демонстрационном ответе БКИ не хватает сведений. '
            'Запросить у клиента справку или пояснение по кредитной истории.')
    else:
        add('R-05 · Кредитная история', 'Демо-ответ БКИ получен',
            'ok', 'Условный ответ БКИ содержит данные; реальная интеграция с бюро в MVP отсутствует.')

    # R-06 Лимит по продукту
    base_limit = PRODUCT_LIMITS.get(app.get('product'), 5_000_000)
    if m['amount'] > base_limit:
        add('R-06 · Лимит продукта', 'Сумма превышает лимит продукта',
            'critical', f"Запрошено {m['amount']:,.0f} ₽ при демонстрационном лимите "
            f"{base_limit:,.0f} ₽ для продукта «{app.get('product')}».".replace(',', ' '))
    elif m['amount'] > m['limit']:
        add('R-06 · Лимит продукта', 'Сумма выше расчётного ориентира',
            'warning', f"Расчётный ориентир по выручке — {m['limit']:,.0f} ₽, "
            f"запрошено {m['amount']:,.0f} ₽.".replace(',', ' '))
    else:
        add('R-06 · Лимит продукта', 'Сумма в пределах ориентира',
            'ok', f"{m['amount']:,.0f} ₽ не превышает расчётный ориентир "
            f"{m['limit']:,.0f} ₽.".replace(',', ' '))

    # R-07 Покрытие выручкой
    if m['revenue'] > 0:
        if m['coverage'] < 1.5:
            add('R-07 · Покрытие выручкой', 'Низкое покрытие долга выручкой',
                'warning', f"Годовая выручка покрывает обслуживание долга {m['coverage']:.1f} раза. "
                'Проверить сезонность и стабильность поступлений.')
        else:
            add('R-07 · Покрытие выручкой', 'Покрытие долга достаточное',
                'ok', f"Годовая выручка покрывает обслуживание долга {m['coverage']:.1f} раза.")

    # R-08 Соотношение суммы и выручки
    if m['revenue'] > 0 and m['amount'] > m['revenue'] * 0.5:
        add('R-08 · Масштаб запроса', 'Сумма сопоставима с годовой выручкой',
            'warning', f"Запрос составляет {m['amount'] / m['revenue']:.0%} годовой выручки — "
            'проверить цель и источники погашения.')

    return results


def score_application(app: dict) -> dict:
    """Индекс качества заявки — не кредитный скоринг, а сводка по демо-правилам."""
    rules = evaluate_application(app)
    critical = sum(1 for r in rules if r['severity'] == 'critical')
    warnings = sum(1 for r in rules if r['severity'] == 'warning')
    score = max(100 - critical * 20 - warnings * 8, 15)
    if score >= 80:
        band, color = 'Данных достаточно для решения', '#15A36A'
    elif score >= 55:
        band, color = 'Требуются уточнения', '#F5A623'
    else:
        band, color = 'Высокий риск, нужна экспертная проверка', '#E0180B'
    return {
        'score': score,
        'band': band,
        'color': color,
        'critical': critical,
        'warnings': warnings,
        'passed': sum(1 for r in rules if r['severity'] == 'ok'),
        'rules': rules,
    }


def next_status_suggestion(app: dict) -> str:
    """Какой шаг логичен дальше — подсказка для сотрудника."""
    result = score_application(app)
    if result['critical']:
        return 'Рекомендуется запросить уточнения или вынести на ручной разбор.'
    if result['warnings']:
        return 'Рекомендуется ручной разбор: сигналы не блокирующие, но требуют проверки.'
    return 'Блокирующих сигналов нет: можно переходить к решению сотрудника.'


def can_transition(current: str, new_status: str) -> bool:
    """Проверка перехода по карте жизненного цикла."""
    return new_status in ALLOWED_TRANSITIONS.get(current, ())
