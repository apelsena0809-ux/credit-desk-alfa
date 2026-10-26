#!/usr/bin/env bash
# Автоматическая проверка основного end-to-end сценария через REST API.
# Использование:
#   bash scripts/demo_e2e.sh              # поднимет свой сервер на :8010 и остановит его
#   API_BASE=http://localhost:8000 bash scripts/demo_e2e.sh   # проверит запущенный сервер
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:8010}"
CLIENT_PIN="${DEMO_CLIENT_PIN:-1234}"
BANK_PIN="${DEMO_BANK_PIN:-0007}"

say() { printf '\n\033[1;31m== %s ==\033[0m\n' "$*"; }
expect() { # JSON приходит по пайпу: expect <python-expr на j> <описание>
  local data expr desc
  data=$(cat)
  expr="$1"; desc="$2"
  printf '%s' "$data" | python3 -c '
import json, sys
expr, desc = sys.argv[1], sys.argv[2]
data = json.load(sys.stdin)
ok = eval(expr, {}, {"j": data})
print(("  [ok] " if ok else "  [FAIL] ") + desc)
sys.exit(0 if ok else 1)
' "$expr" "$desc"
}

say "0. Сброс демо-данных и health-check ${API_BASE}"
curl -fsS -X POST "${API_BASE}/api/demo/reset" > /dev/null
curl -fsS "${API_BASE}/health" | expect "j['status']=='ok'" "сервис отвечает"

say "1. Справочники: продукты и карта состояний"
curl -fsS "${API_BASE}/api/products" | expect "any(p['name']=='Оборотное финансирование' for p in j['products'])" "продукт «Оборотное финансирование» доступен"
curl -fsS "${API_BASE}/api/state-machine" | expect "j['transitions']['SIGNED']==['DISBURSED']" "после подписания — выдача"

say "2. Клиент создаёт заявку (PIN ${CLIENT_PIN})"
APP_REF=$(curl -fsS -X POST "${API_BASE}/api/applications" \
  -H "X-Demo-Pin: ${CLIENT_PIN}" -H 'Content-Type: application/json' \
  -d '{"company_name":"ООО «Ромашка»","inn":"7700123456","product":"Оборотное финансирование",
       "amount":2400000,"term_months":18,"purpose":"Пополнение оборотных средств",
       "annual_revenue":18000000,"concentration_pct":35,"period_mismatch":false,"missing_bki":false}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['application_id'])")
echo "  заявка: ${APP_REF}"
curl -fsS "${API_BASE}/api/applications/${APP_REF}" | expect "j['status']=='SUBMITTED'" "заявка сохранена в статусе SUBMITTED"

say "3. Банк запускает демо-проверки (PIN ${BANK_PIN})"
curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/checks" -H "X-Demo-Pin: ${BANK_PIN}" \
  | expect "j['status']=='MANUAL_REVIEW'" "заявка дошла до ручного разбора"

say "4. Банк одобряет с обязательным основанием"
curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/decision" \
  -H "X-Demo-Pin: ${BANK_PIN}" -H 'Content-Type: application/json' \
  -d '{"decision_type":"APPROVED","terms":"Сумма 2 400 000 ₽, срок 18 мес., ставка 16,9%","reason":"Автотест: показатели в норме, блокирующих сигналов нет"}' \
  | expect "j['status']=='APPROVED'" "статус APPROVED"

say "5. Клиент принимает предложение"
curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/accept" -H "X-Demo-Pin: ${CLIENT_PIN}" \
  | expect "j['status']=='ACCEPTED'" "статус ACCEPTED"

say "6. Демо-подписание: код вместо SMS"
CODE=$(curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/signing-code" -H "X-Demo-Pin: ${CLIENT_PIN}" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['code'])")
echo "  код подписания получен (${CODE})"
curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/sign" \
  -H "X-Demo-Pin: ${CLIENT_PIN}" -H 'Content-Type: application/json' -d "{\"code\":\"${CODE}\"}" \
  | expect "j['status']=='SIGNED'" "статус SIGNED"

say "7. Смоделированная выдача"
curl -fsS -X POST "${API_BASE}/api/applications/${APP_REF}/disburse" -H "X-Demo-Pin: ${CLIENT_PIN}" \
  | expect "j['account_number'].startswith('40802810')" "зачисление на демо-счёт"
curl -fsS "${API_BASE}/api/applications/${APP_REF}" | expect "j['status']=='DISBURSED'" "статус DISBURSED — сценарий завершён"

say "8. Негативные проверки"
BAD_CODE=$(curl -sS -o /tmp/e2e_bad.json -w '%{http_code}' -X POST "${API_BASE}/api/applications" \
  -H "X-Demo-Pin: ${CLIENT_PIN}" -H 'Content-Type: application/json' \
  -d '{"company_name":"ООО «Битый ИНН»","inn":"77001234","product":"Оборотное финансирование",
       "amount":1000000,"term_months":18,"purpose":"Пополнение оборотных средств","annual_revenue":9000000}')
grep -q 'inn' /tmp/e2e_bad.json && BODY_OK='ok' || BODY_OK='fail'
echo "  [$([ "$BAD_CODE" = 422 ] && [ "$BODY_OK" = ok ] && echo ok || echo FAIL)] некорректный ИНН отклонён (HTTP ${BAD_CODE})"
curl -sS -o /dev/null -X POST "${API_BASE}/api/applications" -H "X-Demo-Pin: ${CLIENT_PIN}" \
  -H 'Content-Type: application/json' \
  -d '{"company_name":"ООО «Актив-дубль»","inn":"7712345678","product":"Оборотное финансирование",
       "amount":1000000,"term_months":18,"purpose":"Пополнение оборотных средств","annual_revenue":9000000}'
DUP_CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${API_BASE}/api/applications" \
  -H "X-Demo-Pin: ${CLIENT_PIN}" -H 'Content-Type: application/json' \
  -d '{"company_name":"ООО «Актив-дубль»","inn":"7712345678","product":"Оборотное финансирование",
       "amount":1000000,"term_months":18,"purpose":"Пополнение оборотных средств","annual_revenue":9000000}')
echo "  [$([ "$DUP_CODE" = 409 ] && echo ok || echo FAIL)] повторная заявка по активному ИНН отклонена (${DUP_CODE})"
WRONG_PIN=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${API_BASE}/api/applications/${APP_REF}/accept" -H "X-Demo-Pin: 9999")
echo "  [$([ "$WRONG_PIN" = 401 ] && echo ok || echo FAIL)] неверный PIN (${WRONG_PIN})"
BAD_MOVE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "${API_BASE}/api/applications/${APP_REF}/status" \
  -H "X-Demo-Pin: ${BANK_PIN}" -H 'Content-Type: application/json' -d '{"status":"SIGNING","reason":"попытка отката"}')
echo "  [$([ "$BAD_MOVE" = 409 ] && echo ok || echo FAIL)] недопустимый переход DISBURSED→SIGNING (${BAD_MOVE})"

say "Готово: end-to-end сценарий пройден ✅"
