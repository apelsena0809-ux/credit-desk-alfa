"""ИИ-слой: резюме заявки, команда проверяющих агентов и тексты для клиента.

Модуль сознательно разделяет два режима:
* детерминированный (всегда доступен, без сети) — прозрачные выводы по полям;
* API-режим (если задан OPENAI_API_KEY) — тот же набор фактов передаётся модели.

Ни один режим не принимает кредитного решения: результат — материал для
сотрудника. Сохранён прежний контракт `analyze_application` / `run_agents`,
добавлены помощники для интерфейса.
"""
from __future__ import annotations

import json
import os

from finance import metrics as fin_metrics

# Модель с открытыми весами по умолчанию; замена провайдера — через переменные
# окружения LLM_* (см. .env.example). Подойдёт любой OpenAI-совместимый сервер
# (vLLM, Ollama, LM Studio, OpenRouter) — требование кейса к открытым весам.
MODEL_DEFAULT = 'Qwen/Qwen2.5-7B-Instruct'

SYSTEM_PROMPT = (
    'Ты помощник кредитного аналитика в демонстрационном банковском прототипе. '
    'Не принимай кредитное решение, не придумывай факты и не добавляй данные, которых нет во входном JSON. '
    'Структура ответа: 1) короткое резюме заявки; 2) что проверить в первую очередь; '
    '3) какой документ запросить у клиента. Явно укажи, что вывод требует проверки сотрудником.'
)


def _money(value) -> str:
    try:
        return f'{float(value):,.0f} ₽'.replace(',', ' ')
    except (TypeError, ValueError):
        return '—'


def _signals(app: dict) -> list[str]:
    """Прозрачные сигналы по полям — используются в обоих режимах."""
    signals: list[str] = []
    m = fin_metrics(app)

    if app.get('period_mismatch'):
        signals.append('Несопоставимые отчётные периоды: годовое значение нельзя напрямую сравнивать '
                       'с квартальным — запросить показатели за единый период.')
    concentration = int(app.get('concentration_pct') or 0)
    if concentration >= 50:
        signals.append(f'Поступления концентрированы: на крупнейшего контрагента приходится '
                       f'{concentration}% демонстрационного периода.')
    elif concentration >= 35:
        signals.append(f'Концентрация {concentration}% приближается к порогу 50% — полезна расшифровка поступлений.')
    if app.get('missing_bki'):
        signals.append('В mock-ответе БКИ недостаточно данных; запросить подтверждение у клиента.')
    if m['revenue'] <= 0:
        signals.append('Годовая выручка не указана — оценка обслуживания долга невозможна.')
    elif m['dti'] > 0.35:
        signals.append(f"Обслуживание долга занимает {m['dti']:.0%} годовой выручки — выше демонстрационного порога 35%.")
    elif m['dti'] > 0.25:
        signals.append(f"Обслуживание долга занимает {m['dti']:.0%} годовой выручки — выше комфортных 25%.")
    if m['amount'] > m['limit']:
        signals.append(f"Запрошенная сумма превышает расчётный ориентир по выручке ({_money(m['limit'])}).")
    if not signals:
        signals.append('В заданных демонстрационных полях не выявлены специальные сигналы; '
                       'требуется стандартная проверка источников.')
    return signals


def _facts(app: dict) -> dict:
    m = fin_metrics(app)
    return {
        'application_id': app.get('application_id'),
        'company_name': app.get('company_name'),
        'product': app.get('product'),
        'purpose': app.get('purpose'),
        'amount': app.get('amount'),
        'term_months': app.get('term_months'),
        'annual_revenue': app.get('annual_revenue'),
        'concentration_pct': app.get('concentration_pct'),
        'period_mismatch': bool(app.get('period_mismatch')),
        'missing_bki': bool(app.get('missing_bki')),
        'monthly_payment_demo': round(m['payment']),
        'debt_service_ratio_demo': round(m['dti'], 3),
        'demo_rate_pct': m['rate'],
        'signals': _signals(app),
    }


def _demo_summary(app: dict, facts: dict) -> str:
    m = fin_metrics(app)
    lines = [
        f"**{app.get('company_name')}** запросила **{_money(app.get('amount'))}** на "
        f"{app.get('term_months')} мес. по продукту «{app.get('product')}».",
        f"Годовая выручка — {_money(app.get('annual_revenue'))}; расчётный платёж "
        f"{_money(m['payment'])}/мес. при демонстрационной ставке {m['rate']}%.",
        f"Обслуживание долга — {m['dti']:.0%} годовой выручки.",
        'Ниже — сигналы, которые стоит проверить. Это не решение о выдаче кредита.',
    ]
    return '  \n'.join(lines)


def analyze_application(app: dict) -> dict:
    """Резюме заявки. Возвращает {summary, signals, mode, next_actions}."""
    facts = _facts(app)
    signals = facts['signals']
    api_key = os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY')
    base_url = os.getenv('LLM_BASE_URL') or None
    model = os.getenv('LLM_MODEL', MODEL_DEFAULT)

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': json.dumps(facts, ensure_ascii=False)},
                ],
                temperature=0.2,
                timeout=25,
            )
            return {
                'summary': response.choices[0].message.content,
                'signals': signals,
                'mode': f'ИИ через OpenAI-совместимый API · модель {model} · вывод требует проверки сотрудником',
                'next_actions': _next_actions(app),
                'engine': 'api',
            }
        except Exception as exc:  # noqa: BLE001 — демонстрационный fallback
            return {
                'summary': ('**Не удалось получить ответ внешней модели.** '
                            'Показан прозрачный демонстрационный анализ.'),
                'signals': signals,
                'mode': f'Fallback: {type(exc).__name__}. Проверьте API-ключ, сеть и настройки модели.',
                'next_actions': _next_actions(app),
                'engine': 'fallback',
            }

    return {
        'summary': _demo_summary(app, facts),
        'signals': signals,
        'mode': 'Демо-режим без внешней языковой модели. Для подключения задайте '
                'LLM_API_KEY, LLM_BASE_URL и LLM_MODEL (см. .env.example) — подойдёт '
                'любой OpenAI-совместимый провайдер с моделью открытых весов.',
        'next_actions': _next_actions(app),
        'engine': 'demo',
    }


def _next_actions(app: dict) -> list[str]:
    actions: list[str] = []
    if app.get('period_mismatch'):
        actions.append('Запросить отчётность за единый период (например, 12 месяцев).')
    if int(app.get('concentration_pct') or 0) >= 35:
        actions.append('Запросить расшифровку поступлений по контрагентам и ключевой договор.')
    if app.get('missing_bki'):
        actions.append('Запросить справку об отсутствии задолженности или пояснение по кредитной истории.')
    if fin_metrics(app)['dti'] > 0.25:
        actions.append('Проверить сезонность и стабильность поступлений за 6–12 месяцев.')
    if not actions:
        actions.append('Стандартная проверка источников и первичных документов.')
        actions.append('Зафиксировать основание решения в карточке заявки.')
    return actions


def document_checklist(app: dict) -> list[dict]:
    """Чек-лист документов: что запросить и почему. Без реальной интеграции."""
    items = [
        {'name': 'Устав и карточка компании', 'needed': True,
         'why': 'Базовая проверка реквизитов и полномочий подписанта.'},
        {'name': 'Отчётность за сопоставимый период', 'needed': bool(app.get('period_mismatch')),
         'why': 'Единый период нужен для корректного сравнения показателей.'},
        {'name': 'Расшифровка поступлений по контрагентам', 'needed': int(app.get('concentration_pct') or 0) >= 35,
         'why': 'Подтверждает диверсификацию выручки и устойчивость отношений.'},
        {'name': 'Справка об отсутствии задолженности', 'needed': bool(app.get('missing_bki')),
         'why': 'Закрывает пробел в демонстрационном ответе БКИ.'},
        {'name': 'Договор с ключевым контрагентом', 'needed': int(app.get('concentration_pct') or 0) >= 50,
         'why': 'Подтверждает долгосрочность крупнейшего канала выручки.'},
    ]
    return items


def client_message_draft(app: dict) -> str:
    """Черновик запроса уточнения для клиента — сотрудник может отредактировать."""
    m = fin_metrics(app)
    parts: list[str] = [f"Здравствуйте! По заявке {app.get('application_id', '')} нужны уточнения."]
    if app.get('period_mismatch'):
        parts.append('1. Пришлите финансовые показатели за единый сопоставимый период — '
                     'годовое и квартальное значения сейчас нельзя сравнить напрямую.')
    if int(app.get('concentration_pct') or 0) >= 35:
        parts.append('2. Добавьте расшифровку поступлений по контрагентам за последние 12 месяцев '
                     'и копию договора с крупнейшим покупателем.')
    if app.get('missing_bki'):
        parts.append('3. Приложите справку об отсутствии задолженности или пояснение по кредитной истории.')
    if m['dti'] > 0.25:
        parts.append('4. Поясните сезонность поступлений: в какие месяцы выручка снижается.')
    if len(parts) == 1:
        parts.append('Пришлите, пожалуйста, документы, подтверждающие указанные в заявке показатели.')
    return '\n'.join(parts)


def run_agents(app: dict) -> list[dict]:
    """Пять агентов, каждый проверяет свой аспект. Ни один не выносит решение."""
    amount = float(app.get('amount') or 0)
    revenue = float(app.get('annual_revenue') or 0)
    concentration = int(app.get('concentration_pct') or 0)
    term = int(app.get('term_months') or 0)
    m = fin_metrics(app)
    agents: list[dict] = []

    # 01 — качество заявки
    missing = [
        label for field, label in (
            ('company_name', 'название компании'), ('inn', 'ИНН'),
            ('product', 'кредитный продукт'), ('purpose', 'цель финансирования'),
        ) if not str(app.get(field) or '').strip()
    ]
    if amount <= 0:
        missing.append('сумма финансирования')
    if term <= 0:
        missing.append('срок')
    agents.append({
        'name': 'Агент проверки заявки', 'icon': '01',
        'status': 'Нужны данные' if missing else 'Проверено',
        'tone': 'warn' if missing else 'ok',
        'summary': 'Проверяет заполненность и согласованность полей заявки.',
        'findings': (['Не заполнено: ' + ', '.join(missing)] if missing else
                     [f"Основные поля заполнены. Заявка {app.get('application_id', 'без ID')}: "
                      f"сумма {_money(amount)}, срок {term} мес."]),
        'next_step': ('Вернуть заявку на уточнение обязательных полей.' if missing else
                      'Передать данные следующим агентам без изменения исходных значений.'),
    })

    # 02 — финансовый профиль
    financial: list[str] = []
    if revenue <= 0:
        financial.append('Годовая выручка не указана или равна нулю; оценить профиль по этим данным нельзя.')
    else:
        financial.append(f"Сумма запроса — {amount / revenue:.1%} от указанной годовой выручки.")
        financial.append(f"Расчётный платёж {_money(m['payment'])}/мес.; обслуживание долга — "
                         f"{m['dti']:.0%} годовой выручки.")
    if app.get('period_mismatch'):
        financial.append('Отмечены несопоставимые отчётные периоды — запросить единый период.')
    agents.append({
        'name': 'Финансовый агент', 'icon': '02',
        'status': 'Требует внимания' if revenue <= 0 or app.get('period_mismatch') or m['dti'] > 0.3 else 'Сигналов по полям нет',
        'tone': 'risk' if revenue <= 0 else ('warn' if app.get('period_mismatch') or m['dti'] > 0.3 else 'ok'),
        'summary': 'Сопоставляет запрошенную сумму с выручкой и проверяет периодичность данных.',
        'findings': financial,
        'next_step': 'Проверить первичные финансовые документы и сопоставимость периодов.',
    })

    # 03 — структура выручки
    high = concentration >= 50
    agents.append({
        'name': 'Агент структуры выручки', 'icon': '03',
        'status': 'Требует внимания' if high else 'Сигналов по порогу нет',
        'tone': 'risk' if high else ('warn' if concentration >= 35 else 'ok'),
        'summary': 'Проверяет зависимость бизнеса от крупнейшего контрагента.',
        'findings': [f'Доля крупнейшего контрагента — {concentration}%. ' + (
            'Достигнут демонстрационный порог 50%; важно проверить устойчивость отношений и договорную базу.'
            if high else ('Значение близко к порогу: полезна расшифровка поступлений.'
                          if concentration >= 35 else
                          'Показатель ниже демонстрационного порога 50%; это не подтверждает отсутствие иных рисков.'))],
        'next_step': 'При необходимости запросить расшифровку поступлений по контрагентам и ключевые договоры.',
    })

    # 04 — внешние источники
    agents.append({
        'name': 'Агент проверки источников', 'icon': '04',
        'status': 'Нужны данные' if app.get('missing_bki') else 'Демо-ответ получен',
        'tone': 'warn' if app.get('missing_bki') else 'ok',
        'summary': 'Сводит условные ответы ЕГРЮЛ, ФНС и БКИ; реальных подключений в MVP нет.',
        'findings': [('В демонстрационном ответе БКИ недостаточно данных; запросить пояснение или документ.'
                      if app.get('missing_bki') else
                      'В демонстрационном сценарии БКИ содержит условные данные. Результат не получен из реального бюро.')],
        'next_step': 'Проверить достоверность и актуальность данных через разрешённые банковские интеграции.',
    })

    # 05 — помощник аналитика
    signals: list[str] = []
    if app.get('period_mismatch'):
        signals.append('несопоставимые отчётные периоды')
    if high:
        signals.append('концентрация контрагента 50% или выше')
    if app.get('missing_bki'):
        signals.append('неполные данные БКИ')
    if revenue <= 0:
        signals.append('нет положительной выручки')
    if m['dti'] > 0.35:
        signals.append('высокая долговая нагрузка')
    agents.append({
        'name': 'Агент-помощник аналитика', 'icon': '05',
        'status': 'Нужна ручная проверка' if signals else 'Можно продолжить проверку',
        'tone': 'warn' if signals else 'ok',
        'summary': 'Собирает результаты проверок и предлагает следующий шаг, но не принимает решение.',
        'findings': [('Основные сигналы: ' + '; '.join(signals) + '.') if signals else
                     'По доступным демонстрационным полям специальные сигналы не выявлены. '
                     'Стандартная проверка всё равно обязательна.'],
        'next_step': 'Проверить основания и документы, затем принять решение уполномоченному сотруднику.',
    })
    return agents
