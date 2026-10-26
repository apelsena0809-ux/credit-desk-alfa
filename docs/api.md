# API и проверки

## Два способа проверить решение

1. **Веб-интерфейс** (Streamlit, :8501) — клиентский кабинет, рабочий кабинет
   банка, аналитика. Основной способ.
2. **REST API** (FastAPI, :8000) — тот же жизненный цикл поверх того же слоя
   данных. Swagger UI: <http://localhost:8000/docs>, ReDoc: `/redoc`,
   OpenAPI-схема: `/openapi.json` (копия в `openapi.yaml`).
   Коллекция запросов Postman: `postman_collection.json`.

## Краткое описание API

Базовый URL: `http://localhost:8000`. Аутентификация — демо-PIN в заголовке
`X-Demo-Pin`: `1234` для клиентских операций, `0007` для банковских
(заменяется переменными `DEMO_CLIENT_PIN` / `DEMO_BANK_PIN`).

| Метод и путь | Роль | Назначение |
| --- | --- | --- |
| `GET /health` | — | Проверка живости |
| `GET /api/products` | — | Продукты, демо-ставки, сроки, лимиты, ключевая ставка |
| `GET /api/applications?status=&q=` | — | Список заявок с фильтрами |
| `POST /api/applications` | клиент | Создать заявку (201). 409 — дубль по ИНН, 422 — неверный продукт/срок/ИНН |
| `GET /api/applications/{ref}` | — | Заявка с расчётом (платёж, DTI, индекс качества) |
| `GET /api/applications/{ref}/schedule` | — | График платежей |
| `GET /api/applications/{ref}/audit` | — | Журнал аудита |
| `GET /api/applications/{ref}/checklist` | — | Чек-лист документов |
| `POST /api/applications/{ref}/documents` | клиент | Загрузить документа (multipart) |
| `GET /api/applications/{ref}/documents` | — | Документы заявки |
| `POST /api/applications/{ref}/checks` | банк | Демо-проверки: SUBMITTED → … → MANUAL_REVIEW / NEEDS_INFO |
| `POST /api/applications/{ref}/status` | банк | Сменить статус по карте переходов (409 при недопустимом) |
| `POST /api/applications/{ref}/request-info` | банк | Запросить уточнения (NEEDS_INFO) |
| `POST /api/applications/{ref}/decision` | банк | Решение: APPROVED / CONDITIONAL_APPROVAL / REJECTED / NEEDS_INFO |
| `POST /api/applications/{ref}/accept` | клиент | Принять предложение (ACCEPTED) |
| `POST /api/applications/{ref}/decline` | клиент | Отклонить предложение |
| `POST /api/applications/{ref}/signing-code` | клиент | Получить демо-код подписания |
| `POST /api/applications/{ref}/sign` | клиент | Подписать кодом (SIGNED) |
| `POST /api/applications/{ref}/disburse` | клиент/банк | Смоделированная выдача (DISBURSED) |
| `GET /api/applications/{ref}/disbursement` | — | Реквизиты выдачи |
| `POST /api/ai/summary` | — | ИИ-резюме заявки (LLM опциональна) |
| `POST /api/ai/agents` | — | Результаты пяти ИИ-агентов |
| `GET /api/state-machine` | — | Справочник статусов и карта переходов |

Коды ошибок: 401 — неверный PIN, 404 — заявка не найдена, 409 — недопустимый
переход/дубль, 413 — файл больше 12 МБ, 422 — ошибка валидации.

## Синтетические данные и учётные записи

* Демо-заявки создаются автоматически при первом запуске (см.
  `docs/data-model.md`); сброс — кнопкой в интерфейсе или `POST /api/demo/reset`.
* Тестовые PIN: клиент `1234`, сотрудник банка `0007`.
* Все персональные данные синтетические; загружать реальные — запрещено
  предупреждением в интерфейсе.

## Тестовые сценарии (end-to-end)

Полные пошаговые сценарии с ожидаемыми результатами — `docs/test-scenarios.md`:
happy path до выдачи, отказ, запрос уточнений с возвратом, встречное
предложение, повторная подача после отказа.
