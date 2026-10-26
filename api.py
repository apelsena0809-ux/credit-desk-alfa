"""REST API кредитного кабинета (FastAPI).

Тот же слой данных и те же бизнес-правила, что и у Streamlit-интерфейса:
жизненный цикл заявки валидируется картой переходов `rules.ALLOWED_TRANSITIONS`.

Запуск: uvicorn api:app --host 0.0.0.0 --port 8000
Документация: http://localhost:8000/docs (Swagger UI), /redoc, /openapi.json

Демо-аутентификация (имитация, для проверки сценариев):
* клиентские действия — заголовок `X-Demo-Pin: 1234`;
* действия сотрудника банка — заголовок `X-Demo-Pin: 0007`.

Внешние системы (ЕГРЮЛ, ФНС, БКИ) смоделированы внутри приложения; выдача
кредита — запись о зачислении на демо-счёт, без реальных проводок.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_module import analyze_application, document_checklist, run_agents
from database import (accept_offer, add_audit, create_application, create_signing_code,
                      decline_offer, disburse, get_active_signing_code, get_application,
                      get_audit, get_decision, get_disbursement, get_documents, init_db,
                      inn_has_active_application, list_applications, save_document,
                      set_decision, sign_application, status_counts, update_status)
from finance import KEY_RATE_PCT, KEY_RATE_SOURCE, PRODUCT_LIMITS, PRODUCT_RATES, \
    PRODUCT_TERMS, metrics, payment_schedule
from rules import (ALLOWED_TRANSITIONS, OFFER_STATUSES, STATUS_LABELS,
                   evaluate_application, score_application)

CLIENT_PIN = os.getenv('DEMO_CLIENT_PIN', '1234')
BANK_PIN = os.getenv('DEMO_BANK_PIN', '0007')

app = FastAPI(
    title='Кредитный кабинет предпринимателя — демо API',
    version='1.0.0',
    description=(
        'Учебный REST API хакатонного прототипа: заявка → проверки → решение банка → '
        'принятие предложения → подписание демо-кодом → смоделированная выдача. '
        'Все данные синтетические, внешние системы смоделированы.'
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.on_event('startup')
def _startup() -> None:
    init_db()


# ─────────────────────────────── модели запросов ──────────────────────────────
class ApplicationCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=200, examples=['ООО «Ромашка»'])
    inn: str = Field(min_length=10, max_length=12, examples=['7701234567'])
    product: str = Field(examples=['Оборотное финансирование'])
    amount: float = Field(gt=0, examples=[2_400_000])
    term_months: int = Field(gt=0, le=60, examples=[18])
    purpose: str = Field(examples=['Пополнение оборотных средств'])
    annual_revenue: float = Field(ge=0, examples=[18_000_000])
    concentration_pct: int = Field(ge=0, le=100, default=0)
    period_mismatch: bool = False
    missing_bki: bool = False


class StatusChange(BaseModel):
    status: str = Field(examples=['VALIDATION'])
    reason: str = Field(default='', max_length=500)


class InfoRequest(BaseModel):
    message: str = Field(min_length=10, max_length=2000,
                         examples=['Пришлите расшифровку поступлений по контрагентам.'])


class DecisionIn(BaseModel):
    decision_type: str = Field(examples=['APPROVED'])
    terms: str = Field(default='', max_length=500)
    reason: str = Field(min_length=15, max_length=2000,
                        examples=['Показатели в норме, одобрение на заявленных условиях.'])


class SignIn(BaseModel):
    code: str = Field(min_length=4, max_length=4, examples=['1234'])


class ClarifyIn(BaseModel):
    note: str = Field(min_length=5, max_length=2000,
                      examples=['Приложил(а) расшифровку поступлений и договор с контрагентом.'])


class AiIn(BaseModel):
    application_ref: str = Field(examples=['ALF-26-0003'])


def _app_or_404(ref: str) -> dict:
    found = get_application(ref)
    if found is None:
        raise HTTPException(status_code=404, detail=f'Заявка {ref} не найдена')
    return found


def require_pin(x_demo_pin: str | None = Header(default=None, alias='X-Demo-Pin'),
                expected: str = CLIENT_PIN) -> None:
    """Демо-проверка PIN. В production — авторизация в ДБО / SSO банка."""
    if x_demo_pin != expected:
        raise HTTPException(status_code=401, detail='Неверный демо-PIN (заголовок X-Demo-Pin)')


def client_pin(x_demo_pin: str | None = Header(default=None)) -> None:
    require_pin(x_demo_pin, CLIENT_PIN)


def bank_pin(x_demo_pin: str | None = Header(default=None)) -> None:
    require_pin(x_demo_pin, BANK_PIN)


def _with_calculated(found: dict) -> dict:
    found = dict(found)
    found['metrics'] = metrics(found)
    found['quality_index'] = score_application(found)['score']
    return found


# ─────────────────────────────────── базовые ──────────────────────────────────
@app.get('/health', tags=['Служебные'], summary='Проверка живости сервиса')
def health() -> dict:
    return {'status': 'ok', 'service': 'credit-desk-api', 'demo': True}


@app.get('/api/products', tags=['Справочники'], summary='Кредитные продукты и демо-ставки')
def products() -> dict:
    return {
        'key_rate_pct': KEY_RATE_PCT,
        'key_rate_source': KEY_RATE_SOURCE,
        'products': [
            {'name': name, 'demo_rate_pct': PRODUCT_RATES[name],
             'terms_months': PRODUCT_TERMS.get(name, []),
             'limit_rub': PRODUCT_LIMITS.get(name)}
            for name in PRODUCT_RATES
        ],
    }


@app.get('/api/applications', tags=['Заявки'], summary='Список заявок')
def applications(status: str | None = None, q: str | None = None) -> list[dict]:
    items = list_applications()
    if status:
        items = [a for a in items if a['status'] == status]
    if q:
        needle = q.lower().strip()
        items = [a for a in items if needle in f"{a['application_id']} {a['company_name']} {a['inn']}".lower()]
    return [_with_calculated(a) for a in items]


@app.post('/api/applications', tags=['Заявки'], status_code=201,
          summary='Создать заявку (клиент)', dependencies=[Depends(client_pin)])
def create(payload: ApplicationCreate) -> dict:
    digits = ''.join(ch for ch in payload.inn if ch.isdigit())
    if len(digits) not in (10, 12):
        raise HTTPException(status_code=422, detail='ИНН должен содержать 10 цифр (юрлицо) или 12 (ИП)')
    if payload.product not in PRODUCT_RATES:
        raise HTTPException(status_code=422, detail=f'Неизвестный продукт. Доступно: {list(PRODUCT_RATES)}')
    if payload.term_months not in PRODUCT_TERMS.get(payload.product, []):
        raise HTTPException(status_code=422,
                            detail=f"Срок {payload.term_months} мес. недоступен для продукта "
                                   f"«{payload.product}». Доступные сроки: {PRODUCT_TERMS.get(payload.product)}")
    duplicate = inn_has_active_application(digits)
    if duplicate:
        raise HTTPException(status_code=409, detail=f"По ИНН {digits} уже есть активная заявка "
                                                    f"{duplicate['application_id']}")
    pk = create_application(payload.company_name.strip(), digits, payload.product, payload.amount,
                            payload.term_months, payload.purpose.strip(), payload.annual_revenue,
                            payload.concentration_pct, payload.period_mismatch, payload.missing_bki)
    add_audit(pk, 'client', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED', 'Заявка отправлена клиентом')
    update_status(pk, 'SUBMITTED', actor='client', reason='Заявка отправлена клиентом', log=False)
    return _with_calculated(get_application(pk))


@app.get('/api/applications/{ref}', tags=['Заявки'], summary='Заявка по номеру или id')
def application(ref: str) -> dict:
    return _with_calculated(_app_or_404(ref))


@app.get('/api/applications/{ref}/schedule', tags=['Заявки'], summary='График платежей')
def schedule(ref: str, limit: int | None = None) -> list[dict]:
    found = _app_or_404(ref)
    return payment_schedule(found, limit=limit).to_dict(orient='records')


@app.get('/api/applications/{ref}/audit', tags=['Жизненный цикл'], summary='Журнал аудита')
def audit(ref: str) -> list[dict]:
    return get_audit(_app_or_404(ref)['id'])


@app.get('/api/applications/{ref}/documents', tags=['Документы'], summary='Документы заявки')
def documents(ref: str) -> list[dict]:
    return get_documents(_app_or_404(ref)['id'])


@app.post('/api/applications/{ref}/documents', tags=['Документы'], status_code=201,
          summary='Загрузить документ (клиент)', dependencies=[Depends(client_pin)])
def upload_document(ref: str, file: UploadFile = File(...)) -> dict:
    found = _app_or_404(ref)
    content = file.file.read()
    if not save_document(found['id'], file.filename or 'document', content,
                         file.content_type or 'application/octet-stream'):
        raise HTTPException(status_code=413, detail='Файл больше 12 МБ или пустой')
    add_audit(found['id'], 'client', 'client', 'DOCUMENT_UPLOADED', None, None,
              f'Загружен документ: {file.filename}')
    return {'application_id': found['application_id'], 'filename': file.filename,
            'size_bytes': len(content)}


@app.get('/api/applications/{ref}/checklist', tags=['Документы'],
         summary='Чек-лист документов по заявке')
def checklist(ref: str) -> list[dict]:
    return document_checklist(_app_or_404(ref))


# ─────────────────────────────── обработка банком ─────────────────────────────
@app.post('/api/applications/{ref}/checks', tags=['Жизненный цикл'],
          summary='Запустить демо-проверки (банк)',
          description='SUBMITTED → VALIDATION → ENRICHMENT → SCORING → '
                      'NEEDS_INFO | MANUAL_REVIEW. Внешние источники смоделированы.')
def run_checks(ref: str, x_demo_pin: str | None = Header(default=None, alias='X-Demo-Pin')) -> dict:
    require_pin(x_demo_pin, BANK_PIN)
    found = _app_or_404(ref)
    current = found['status']
    if current not in ('SUBMITTED', 'VALIDATION', 'ENRICHMENT', 'SCORING', 'NEEDS_INFO'):
        raise HTTPException(status_code=409, detail=f'Проверки уже выполнены (статус {current})')
    if current == 'SUBMITTED':
        update_status(found['id'], 'VALIDATION', actor='analyst', reason='Начата формальная проверка')
    update_status(found['id'], 'ENRICHMENT', actor='analyst',
                  reason='Формальная проверка завершена; запрос демо-источников')
    update_status(found['id'], 'SCORING', actor='analyst', reason='Демо-источники получены; расчёт показателей')
    if found['missing_bki']:
        update_status(found['id'], 'NEEDS_INFO', actor='analyst',
                      reason='В демо-ответе БКИ недостаточно данных',
                      client_message='Пожалуйста, приложите справку об отсутствии задолженности '
                                     'или пояснение по кредитной истории.')
    else:
        update_status(found['id'], 'MANUAL_REVIEW', actor='analyst',
                      reason='Демо-проверки завершены; ожидает решения сотрудника')
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/status', tags=['Жизненный цикл'],
          summary='Сменить статус (банк)', dependencies=[Depends(bank_pin)])
def change_status(ref: str, payload: StatusChange) -> dict:
    found = _app_or_404(ref)
    if payload.status not in ALLOWED_TRANSITIONS.get(found['status'], ()):
        raise HTTPException(status_code=409,
                            detail=f"Переход {found['status']} → {payload.status} не разрешён. "
                                   f"Допустимо: {list(ALLOWED_TRANSITIONS.get(found['status'], ()))}")
    update_status(found['id'], payload.status, actor='analyst',
                  reason=payload.reason or 'Изменение статуса сотрудником банка')
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/request-info', tags=['Жизненный цикл'],
          summary='Запросить уточнения (банк)', dependencies=[Depends(bank_pin)])
def request_info(ref: str, payload: InfoRequest) -> dict:
    found = _app_or_404(ref)
    if 'NEEDS_INFO' not in ALLOWED_TRANSITIONS.get(found['status'], ()):
        raise HTTPException(status_code=409, detail=f"Из статуса {found['status']} нельзя запросить уточнения")
    add_audit(found['id'], 'bank', 'analyst', 'INFO_REQUESTED', found['status'], 'NEEDS_INFO', payload.message)
    update_status(found['id'], 'NEEDS_INFO', actor='analyst', reason='Запрошены дополнительные сведения',
                  client_message=payload.message, log=False)
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/decision', tags=['Жизненный цикл'],
          summary='Зафиксировать кредитное решение (банк)', dependencies=[Depends(bank_pin)])
def decision(ref: str, payload: DecisionIn) -> dict:
    found = _app_or_404(ref)
    if found['status'] != 'MANUAL_REVIEW':
        raise HTTPException(status_code=409,
                            detail=f"Решение принимается только из статуса MANUAL_REVIEW, "
                                   f"текущий — {found['status']}")
    if payload.decision_type not in ('APPROVED', 'CONDITIONAL_APPROVAL', 'REJECTED', 'NEEDS_INFO'):
        raise HTTPException(status_code=422, detail='Недопустимый тип решения')
    set_decision(found['id'], payload.decision_type, payload.terms, payload.reason.strip(), 'senior_analyst')
    update_status(found['id'], payload.decision_type, actor='senior_analyst', reason=payload.reason.strip())
    return _with_calculated(get_application(found['id']))


# ─────────────────────────────── сделка: клиент ───────────────────────────────
@app.post('/api/applications/{ref}/accept', tags=['Сделка'],
          summary='Принять кредитное предложение (клиент)', dependencies=[Depends(client_pin)])
def accept(ref: str) -> dict:
    found = _app_or_404(ref)
    if found['status'] not in OFFER_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"Принять предложение можно в статусах {OFFER_STATUSES}, "
                                   f"текущий — {found['status']}")
    accept_offer(found['id'])
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/decline', tags=['Сделка'],
          summary='Отклонить одобренное предложение (клиент)', dependencies=[Depends(client_pin)])
def decline(ref: str) -> dict:
    found = _app_or_404(ref)
    if found['status'] not in OFFER_STATUSES:
        raise HTTPException(status_code=409, detail=f"Отклонить предложение можно в статусах "
                                                    f"{OFFER_STATUSES}, текущий — {found['status']}")
    decline_offer(found['id'])
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/signing-code', tags=['Сделка'],
          summary='Получить демо-код подписания (клиент)',
          description='Имитация SMS/пуша: код возвращается в ответе и виден в интерфейсе. '
                      'Заявка переходит в SIGNING. Повторный вызов перевыпускает код. '
                      'Реальная ЭП и доставка сообщений не реализованы.')
def signing_code(ref: str, x_demo_pin: str | None = Header(default=None, alias='X-Demo-Pin')) -> dict:
    require_pin(x_demo_pin, CLIENT_PIN)
    found = _app_or_404(ref)
    if found['status'] not in ('ACCEPTED', 'SIGNING'):
        raise HTTPException(status_code=409, detail=f"Код выдаётся после принятия предложения (ACCEPTED), "
                                                    f"текущий статус — {found['status']}")
    code = create_signing_code(found['id'])
    return {'application_id': found['application_id'],
            'note': 'Демо-код вместо SMS; в production доставляется банком',
            'code': code}


@app.post('/api/applications/{ref}/sign', tags=['Сделка'],
          summary='Подписать предложение кодом (клиент)', dependencies=[Depends(client_pin)])
def sign(ref: str, payload: SignIn) -> dict:
    found = _app_or_404(ref)
    if found['status'] not in ('ACCEPTED', 'SIGNING'):
        raise HTTPException(status_code=409, detail=f"Подписание доступно после принятия предложения, "
                                                    f"текущий — {found['status']}")
    if not sign_application(found['id'], payload.code):
        raise HTTPException(status_code=422, detail='Код неверный или истёк — запросите новый')
    return _with_calculated(get_application(found['id']))


@app.post('/api/applications/{ref}/disburse', tags=['Сделка'],
          summary='Смоделированная выдача кредита',
          description='Создаёт запись о зачислении на демо-счёт. Реальные проводки не выполняются. '
                      'Основной сценарий завершается здесь; посткредитное обслуживание не входит в MVP.')
def do_disburse(ref: str, x_demo_pin: str | None = Header(default=None, alias='X-Demo-Pin')) -> dict:
    if x_demo_pin not in (CLIENT_PIN, BANK_PIN):
        raise HTTPException(status_code=401, detail='Неверный демо-PIN (заголовок X-Demo-Pin)')
    found = _app_or_404(ref)
    if found['status'] not in ('SIGNED', 'DISBURSED'):
        raise HTTPException(status_code=409, detail=f"Выдача доступна после подписания (SIGNED), "
                                                    f"текущий статус — {found['status']}")
    return get_disbursement(found['id']) or disburse(found['id'])


@app.get('/api/applications/{ref}/disbursement', tags=['Сделка'], summary='Реквизиты выдачи')
def disbursement(ref: str) -> dict:
    found = _app_or_404(ref)
    record = get_disbursement(found['id'])
    if record is None:
        raise HTTPException(status_code=404, detail='Выдача ещё не проводилась')
    return record


# ─────────────────────────────────── ИИ ───────────────────────────────────────
@app.post('/api/applications/{ref}/clarify', tags=['Жизненный цикл'],
          summary='Отправить уточнение по запросу банка (клиент)',
          dependencies=[Depends(client_pin)])
def clarify(ref: str, payload: ClarifyIn) -> dict:
    """Клиент отвечает на запрос уточнений — заявка возвращается на обработку."""
    found = _app_or_404(ref)
    if found['status'] != 'NEEDS_INFO':
        raise HTTPException(status_code=409, detail=f"Уточнение ожидается в статусе NEEDS_INFO, "
                                                    f"текущий — {found['status']}")
    add_audit(found['id'], 'client', 'client', 'CLIENT_CLARIFICATION_SUBMITTED',
              found['status'], 'ENRICHMENT', payload.note.strip())
    update_status(found['id'], 'ENRICHMENT', actor='client', reason='Клиент предоставил уточнение')
    return _with_calculated(get_application(found['id']))


@app.post('/api/demo/reset', tags=['Служебные'], summary='Сбросить демо-данные')
def demo_reset() -> dict:
    from database import reset_demo_data

    reset_demo_data()
    return {'status': 'ok', 'detail': 'Демо-данные пересозданы (5 синтетических заявок)'}


@app.post('/api/ai/summary', tags=['ИИ'], summary='ИИ-резюме заявки для сотрудника')
def ai_summary(payload: AiIn) -> dict:
    return analyze_application(_app_or_404(payload.application_ref))


@app.post('/api/ai/agents', tags=['ИИ'], summary='Команда ИИ-агентов (пять проверок)')
def ai_agents(payload: AiIn) -> list[dict]:
    return run_agents(_app_or_404(payload.application_ref))


@app.get('/api/state-machine', tags=['Справочники'], summary='Карта переходов жизненного цикла')
def state_machine() -> dict:
    return {'statuses': STATUS_LABELS, 'transitions': ALLOWED_TRANSITIONS}


@app.exception_handler(ValueError)
def value_error_handler(request, exc: ValueError):  # pragma: no cover
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.exception_handler(RequestValidationError)
def validation_error_handler(request, exc: RequestValidationError):  # pragma: no cover
    """Ошибки валидации в человекочитаемом виде: поле — описание."""
    details = '; '.join(
        '.'.join(str(part) for part in err.get('loc', [])) + f": {err.get('msg')}"
        for err in exc.errors()
    )
    return JSONResponse(status_code=422, content={'detail': details})
