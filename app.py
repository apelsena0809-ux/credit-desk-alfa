"""Кредитный кабинет — демонстрационный MVP для предпринимателя и банка.

Запуск:  docker compose up --build   (web + api)  или  streamlit run app.py
Данные синтетические, база — локальная SQLite (data/credit_desk.db).
Сквозной сценарий: заявка → проверки → решение → принятие предложения →
подписание (демо-код вместо ЭП) → смоделированная выдача.
REST API: FastAPI (api.py), Swagger — http://localhost:8000/docs.
Интерфейс стилизован под визуальный язык Альфа-Банка, но не является
официальным продуктом банка; логотип не используется.
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

import theme
from ai_module import (analyze_application, client_message_draft, document_checklist,
                       run_agents)
from database import (accept_offer, add_audit, create_application, create_signing_code,
                      decline_offer, disburse, get_active_signing_code, get_application,
                      get_audit, get_decision, get_disbursement, get_documents, init_db,
                      inn_has_active_application, list_applications, reset_demo_data,
                      save_document, set_decision, sign_application, status_counts, update_status)
from finance import PRODUCT_LIMITS, PRODUCT_RATES, PRODUCT_TERMS, KEY_RATE_PCT, affordability, metrics, payment_schedule
from rules import (ACTIVE_STATUSES, ALLOWED_TRANSITIONS, OFFER_STATUSES, SEVERITY_LABELS,
                   STATUS_LABELS, STATUS_OPTIONS, STATUS_TONE, evaluate_application,
                   score_application)

st.set_page_config(page_title='Кредитный кабинет · демо', page_icon='🔴', layout='wide',
                   initial_sidebar_state='expanded')
init_db()
theme.inject_theme()

MODES = ['Клиентский кабинет', 'Рабочий кабинет банка', 'Аналитика портфеля', 'Описание проекта']
ROLE_LABELS = {'analyst': 'Аналитик', 'senior_analyst': 'Старший аналитик', 'admin': 'Администратор'}

# Тестовые учётные записи для проверки end-to-end сценария (см. README).
# Аутентификация смоделирована: регистрация по кейсу не нужна —
# рассматривается действующий клиент банка. PIN переопределяется
# переменными окружения DEMO_CLIENT_PIN / DEMO_BANK_PIN.
DEMO_ACCOUNTS = {
    'client': {'pin': os.getenv('DEMO_CLIENT_PIN', '1234'), 'label': 'Клиент · ИП Соколов А. В.'},
    'bank': {'pin': os.getenv('DEMO_BANK_PIN', '0007'), 'label': 'Сотрудник банка · любая роль'},
}

fmt_money = theme.money
status_text = lambda s: STATUS_LABELS.get(s, s)  # noqa: E731


def chip(status: str) -> str:
    return theme.status_chip(status)


def auth_gate(namespace: str, title: str) -> None:
    """Демонстрационный вход по PIN.

    Регистрация не нужна по кейсу: рассматривается действующий клиент банка.
    В production на этом месте — вход в ДБО; здесь процесс смоделирован.
    """
    if st.session_state.get(f'auth_{namespace}'):
        return
    st.markdown(f'<div class="card-title" style="font-size:1.2rem">{theme.esc(title)}</div>',
                unsafe_allow_html=True)
    st.caption('Регистрация не предусмотрена кейсом: рассматривается действующий клиент банка. '
               'Вход смоделирован демо-PIN; в production это авторизация в ДБО/приложении банка.')
    st.info(f"Тестовая учётная запись: {DEMO_ACCOUNTS[namespace]['label']} · PIN: "
            f"**{DEMO_ACCOUNTS[namespace]['pin']}**")
    entered = st.text_input('Демо-PIN', type='password', key=f'pin_{namespace}')
    c_left, _ = st.columns([1, 2])
    with c_left:
        if st.button('Войти', type='primary'):
            if entered.strip() == DEMO_ACCOUNTS[namespace]['pin']:
                st.session_state[f'auth_{namespace}'] = True
                st.rerun()
            else:
                st.error('Неверный PIN. Тестовые коды указаны в README.')
    st.stop()


# ─────────────────────────────── боковая панель ───────────────────────────────
with st.sidebar:
    st.markdown(theme.sidebar_brand(), unsafe_allow_html=True)
    mode = st.radio('Режим', MODES, label_visibility='collapsed', key='mode')

    role = 'client'
    if mode == 'Рабочий кабинет банка':
        st.markdown('<div class="note" style="margin-top:6px">Роль сотрудника</div>', unsafe_allow_html=True)
        role = st.selectbox('Роль сотрудника', list(ROLE_LABELS), format_func=lambda r: ROLE_LABELS[r],
                            label_visibility='collapsed')

    apps_all = list_applications()
    counts = status_counts()
    active = sum(counts.get(s, 0) for s in ACTIVE_STATUSES)
    needs = counts.get('NEEDS_INFO', 0) + sum(counts.get(s, 0) for s in OFFER_STATUSES)
    manual = counts.get('MANUAL_REVIEW', 0)
    total_volume = sum(float(a['amount']) for a in apps_all)

    st.markdown(
        f"""
        <div class="side-stat" style="margin-top:14px">
          <div class="r"><span>Всего заявок</span><b>{len(apps_all)}</b></div>
          <div class="r"><span>Активных</span><b>{active}</b></div>
          <div class="r"><span>На ручном разборе</span><b>{manual}</b></div>
          <div class="r"><span>Ждут клиента</span><b>{needs}</b></div>
          <div class="r" style="border-top:1px solid #EEE;margin-top:5px;padding-top:6px">
            <span>Портфель</span><b>{theme.short_money(total_volume)}</b>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander('Данные и сброс'):
        st.caption('База SQLite в папке data/. Все записи синтетические.')
        if st.button('Сбросить демо-данные', use_container_width=True):
            reset_demo_data()
            for key in [k for k in st.session_state if k.startswith(('agents_result_', 'ai_result_'))]:
                st.session_state.pop(key, None)
            st.rerun()

    st.markdown(
        '<div class="note" style="margin-top:10px">Демонстрационный прототип. '
        'Не используйте реальные персональные и банковские данные.</div>',
        unsafe_allow_html=True,
    )


# ───────────────────────────────── утилиты ────────────────────────────────────
def app_header(app: dict, subtitle: str = '') -> None:
    st.markdown(
        f'<div style="margin:0 0 4px"><span class="chip chip-neutral">Заявка {theme.esc(app["application_id"])}</span> '
        f'&nbsp;{chip(app["status"])}</div>'
        f'<div class="card-title" style="font-size:1.35rem;margin-top:8px">{theme.esc(app["company_name"])}</div>'
        f'<div class="card-sub">{theme.esc(app["product"])} · ИНН {theme.esc(app["inn"])}'
        f'{" · " + theme.esc(subtitle) if subtitle else ""}</div>',
        unsafe_allow_html=True,
    )


def risk_badge(result: dict) -> str:
    tone = 'chip-ok' if result['score'] >= 80 else ('chip-warn' if result['score'] >= 55 else 'chip-risk')
    return f'<span class="chip {tone}">{theme.esc(result["band"])}</span>'


# ────────────────────────────── описание проекта ──────────────────────────────
if mode == 'Описание проекта':
    theme.hero('Кредитный кабинет предпринимателя',
               'Демонстрационный MVP: заявка, проверки, прозрачные правила, решение банка, '
               'принятие предложения, смоделированные подписание и выдача. '
               'Визуальный язык вдохновлён финтех-интерфейсами Альфа-Банка — это не официальный продукт банка.')

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        theme.kpi('Заявок в базе', str(len(apps_all)), 'синтетические данные')
    with c2:
        theme.kpi('Правил проверки', '8', 'R-01 … R-08', accent=True)
    with c3:
        theme.kpi('ИИ-агентов', '5', 'без права решения')
    with c4:
        theme.kpi('Статусов жизненного цикла', str(len(STATUS_LABELS)), 'до смоделированной выдачи')

    left, right = st.columns([1.15, 1], gap='large')
    with left:
        theme.section('Что реализовано')
        st.markdown(
            '- Сквозной сценарий: потребность в финансировании → заявка → проверки → решение банка → '
            'принятие предложения клиентом → подписание демо-кодом → смоделированная выдача.\n'
            '- Выбор кредитного продукта с рыночными демо-ставками (от ключевой ставки ЦБ 14%).\n'
            '- Сохранность всех данных в SQLite: заявки, статусы, документы, решения, выдачи, аудит.\n'
            '- Обе стороны процесса: клиентский кабинет и обработка заявки банком с движением по этапам.\n'
            '- Все исходы: одобрение, отказ, одобрение с условиями (встречное предложение), запрос уточнений.\n'
            '- Внешние проверки (ЕГРЮЛ, ФНС, БКИ) — через моки; интеграций с системами банка нет.\n'
            '- Восемь прозрачных бизнес-правил, отделённых от ИИ и от скоринга.\n'
            '- Команда из пяти ИИ-агентов: качество, финансы, структура выручки, источники, помощник.\n'
            '- Демо-вход по PIN (тестовые учётные записи в README), роли сотрудников.\n'
            '- REST API на FastAPI со Swagger-документацией: тот же жизненный цикл, что в интерфейсе.\n'
            '- Дашборд портфеля: объём, конверсия, распределение по статусам и продуктам.\n'
        )
        theme.section('Демонстрационный сценарий (end-to-end)')
        st.markdown(
            '1. **Клиентский кабинет** (PIN 1234) → «Новая заявка» → расчёт в реальном времени → отправка.\n'
            '2. **Рабочий кабинет банка** (PIN 0007) → заявка в очереди → демо-проверки, моки ЕГРЮЛ/ФНС/БКИ.\n'
            '3. Проверьте разные исходы: одобрение, отказ, запрос уточнений, встречное предложение.\n'
            '4. **Клиент** принимает предложение → получает демо-код подписания (имитация SMS) → подписывает.\n'
            '5. **Смоделированная выдача**: запись о зачислении на демо-счёт. Основной сценарий завершён.\n'
            '6. Альтернатива через API: Swagger на http://localhost:8000/docs или коллекция Postman из репозитория.\n'
        )
    with right:
        theme.section('Границы прототипа и допущения')
        st.warning('Нет реальных интеграций с ЕГРЮЛ, ФНС и БКИ — только моки. '
                   'Подписание смоделировано демо-кодом внутри продукта (без SMS и ЭП). '
                   'Выдача — запись о зачислении без реальных проводок. '
                   'Аутентификация — демо-PIN, без серверного разграничения доступа в UI-режиме. '
                   'Правила и ставки демонстрационные и не описывают кредитную политику банка.')
        theme.section('Модели и сервисы')
        st.markdown('LLM — опционально, любой OpenAI-совместимый провайдер с моделью открытых весов '
                    '(настраивается через `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`). '
                    'По умолчанию решение работает полностью детерминированно, без внешней модели.')
        theme.section('Стек')
        st.markdown('`streamlit` · `fastapi` · `pandas` · `openai` (опционально) · `sqlite3` · `docker compose`')
        st.caption('Товарные знаки и логотип Альфа-Банка не используются; '
                   'фирменная палитра воспроизведена как визуальная стилизация. '
                   'Документация: docs/architecture.md, docs/api.md, docs/state-diagram.md, docs/data-model.md, '
                   'openapi.yaml, postman_collection.json.')


# ─────────────────────────────── клиентский кабинет ───────────────────────────
elif mode == 'Клиентский кабинет':
    auth_gate('client', 'Вход в кабинет предпринимателя')
    theme.hero('Кабинет предпринимателя',
               'Заявка на финансирование, прозрачные условия и статус в реальном времени.',
               kicker='Альфа-Бизнес · демонстрация')

    my_apps = apps_all
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        theme.kpi('Мои заявки', str(len(my_apps)), 'в демо-базе')
    with c2:
        theme.kpi('В работе', str(sum(1 for a in my_apps if a['status'] in ACTIVE_STATUSES)), 'активные заявки')
    with c3:
        theme.kpi('Ждут моего ответа',
                  str(sum(1 for a in my_apps if a['status'] == 'NEEDS_INFO'
                          or a['status'] in OFFER_STATUSES)),
                  'уточнения или предложение', accent=True)
    with c4:
        theme.kpi('Выдано кредитов',
                  str(sum(1 for a in my_apps if a['status'] == 'DISBURSED')),
                  'смоделированная выдача')

    tab1, tab2 = st.tabs(['Мои заявки', 'Новая заявка'])

    with tab1:
        if not my_apps:
            st.info('Пока нет заявок. Создайте первую во вкладке «Новая заявка».')
        for app in my_apps:
            m = metrics(app)
            with st.container(border=True):
                head_l, head_r = st.columns([3, 1.05])
                with head_l:
                    st.markdown(
                        f'<div class="card-title">{theme.esc(app["company_name"])}</div>'
                        f'<div class="card-sub">Заявка {theme.esc(app["application_id"])} · '
                        f'{theme.esc(app["product"])} · {app["term_months"]} мес. · от {theme.esc(app["created_at"][:10])}</div>',
                        unsafe_allow_html=True)
                with head_r:
                    st.markdown(f'<div style="text-align:right">{chip(app["status"])}</div>', unsafe_allow_html=True)

                st.markdown(theme.stepper(app['status']), unsafe_allow_html=True)

                k1, k2, k3, k4 = st.columns(4)
                with k1:
                    theme.kpi('Сумма запроса', fmt_money(app['amount']), f'{m["rate"]}% демо-ставка')
                with k2:
                    theme.kpi('Платёж в месяц', fmt_money(m['payment']), f'{app["term_months"]} мес.')
                with k3:
                    theme.kpi('Нагрузка на выручку', f'{m["dti"]:.0%}', 'обслуживание долга')
                with k4:
                    theme.kpi('Цель', app['purpose'].split(' ')[0], app['purpose'])

                with st.expander('Подробности и расчёт'):
                    d1, d2 = st.columns([1, 1.25])
                    with d1:
                        st.markdown(
                            f'**ИНН:** {theme.esc(app["inn"])}  \n'
                            f'**Годовая выручка:** {fmt_money(app["annual_revenue"])}  \n'
                            f'**Доля крупнейшего контрагента:** {app["concentration_pct"]}%  \n'
                            f'**Переплата за срок:** {fmt_money(m["interest"])} '
                            f'({m["overpayment_share"]:.0%} от суммы)'
                        )
                        st.caption('Показатели демо-проверок: '
                                   + ('несопоставимые отчётные периоды; ' if app['period_mismatch']
                                      else 'периоды сопоставимы; ')
                                   + ('в ответе БКИ не хватает данных' if app['missing_bki']
                                      else 'ответ БКИ содержит данные'))
                    with d2:
                        st.caption('График платежей (первые 6 месяцев)')
                        st.dataframe(payment_schedule(app, limit=6), hide_index=True, use_container_width=True)

                if app['status'] == 'NEEDS_INFO':
                    st.warning(app.get('client_message') or 'Банку нужны дополнительные сведения.')
                    with st.form(f'upload_{app["id"]}'):
                        note = st.text_area('Пояснение для банка', key=f'note_{app["id"]}',
                                            placeholder='Коротко опишите, что уточняете и что приложили')
                        uploaded = st.file_uploader(
                            'Прикрепить документ (PDF, DOCX, XLSX, PNG, JPG, TXT)',
                            type=['pdf', 'docx', 'xlsx', 'png', 'jpg', 'jpeg', 'txt'],
                            key=f'file_{app["id"]}')
                        submitted = st.form_submit_button('Отправить уточнение', type='primary')
                        if submitted:
                            if not note.strip() and uploaded is None:
                                st.error('Добавьте пояснение или прикрепите документ.')
                            elif uploaded is not None and len(uploaded.getvalue()) > 12 * 1024 * 1024:
                                st.error('Файл больше 12 МБ. Приложите документ меньшего размера.')
                            else:
                                if uploaded is not None and not save_document(
                                        app['id'], uploaded.name, uploaded.getvalue(),
                                        uploaded.type or 'application/octet-stream'):
                                    st.error('Не удалось сохранить документ.')
                                else:
                                    add_audit(app['id'], 'client', 'client', 'CLIENT_CLARIFICATION_SUBMITTED',
                                              app['status'], 'ENRICHMENT',
                                              note.strip() or 'Документ приложен')
                                    update_status(app['id'], 'ENRICHMENT', actor='client',
                                                  reason='Клиент предоставил уточнение')
                                    st.success('Уточнение сохранено. Заявка возвращена на обработку.')
                                    st.rerun()

                # ── сделка: принятие предложения → подписание → выдача ──
                elif app['status'] in OFFER_STATUSES:
                    d0 = get_decision(app['id'])
                    st.success('🎉 Одобрено! ' + (f'Условия: {d0["terms"]}. ' if d0 and d0.get('terms') else '')
                               + 'Примите предложение, чтобы продолжить.')
                    st.caption('Дальше по сценарию: подписание демо-кодом (имитация ЭП) '
                               'и смоделированная выдача. Реальные проводки не выполняются.')
                    b1, b2 = st.columns(2)
                    with b1:
                        if st.button('Принять предложение и продолжить', key=f'accept_{app["id"]}',
                                     type='primary', use_container_width=True):
                            accept_offer(app['id'])
                            st.rerun()
                    with b2:
                        if st.button('Отклонить предложение', key=f'decline_{app["id"]}',
                                     use_container_width=True):
                            decline_offer(app['id'])
                            st.rerun()

                elif app['status'] in ('ACCEPTED', 'SIGNING'):
                    code_info = get_active_signing_code(app['id'])
                    st.info('Предложение принято. Подпишите кредитное предложение кодом — '
                            'имитация SMS/пуша вместо реальной ЭП.')
                    if code_info is None:
                        if st.button('Получить код подписания', key=f'code_{app["id"]}',
                                     type='primary', use_container_width=True):
                            create_signing_code(app['id'])
                            st.rerun()
                    else:
                        st.caption(f'Демо-код подбрасывается в интерфейс вместо SMS: '
                                   f'**{code_info["code"]}** · действует до {code_info["expires_at"]}')
                        entered_code = st.text_input('Код подписания', key=f'code_in_{app["id"]}',
                                                     max_chars=4)
                        if st.button('Подписать предложение', key=f'sign_{app["id"]}',
                                     type='primary', use_container_width=True):
                            if sign_application(app['id'], entered_code):
                                st.success('Предложение подписано.')
                                st.rerun()
                            else:
                                st.error('Код неверный или истёк. Запросите новый.')

                elif app['status'] == 'SIGNED':
                    st.info('Документы подписаны. Осталось получить кредит — выдача смоделирована '
                            'записью о зачислении на демо-счёт, без реальных проводок.')
                    if st.button('Получить кредит (демо-выдача)', key=f'disb_{app["id"]}',
                                 type='primary', use_container_width=True):
                        disburse(app['id'])
                        st.rerun()

                elif app['status'] == 'DISBURSED':
                    d_rec = get_disbursement(app['id'])
                    if d_rec:
                        st.success(f'✅ Кредит выдан (смоделированная выдача). '
                                   f'Демо-счёт {d_rec["account_number"]} · '
                                   f'{theme.money(d_rec["amount"])} · {d_rec["disbursed_at"]}')
                        st.caption('Основной сценарий завершён: от потребности в финансировании '
                                   'до факта выдачи. Посткредитное обслуживание не входит в MVP.')

                with st.expander('История статусов и документы'):
                    events = get_audit(app['id'])
                    st.markdown(theme.timeline(events), unsafe_allow_html=True)
                    docs = get_documents(app['id'])
                    if docs:
                        st.markdown('**Документы:** ' + ', '.join(d['filename'] for d in docs))
                    decision = get_decision(app['id'])
                    if decision:
                        st.success(f"Результат: {status_text(decision['decision_type'])} — {decision['reason']}")
                        if decision.get('terms'):
                            st.write(f"Условия: {decision['terms']}")

    with tab2:
        st.session_state.setdefault('form_version', 0)
        version = st.session_state['form_version']

        st.markdown('<div class="card-title" style="margin-bottom:2px">Параметры финансирования</div>'
                    '<div class="card-sub">Все данные демонстрационные и сохраняются только в локальной базе.</div>',
                    unsafe_allow_html=True)

        form_col, calc_col = st.columns([1.35, 1], gap='large')

        with form_col:
            with st.form(f'new_application_{version}'):
                f1, f2 = st.columns([1.5, 1])
                company = f1.text_input('Название компании', placeholder='ООО «Северный склад»',
                                        key=f'company_{version}')
                inn = f2.text_input('ИНН (демо)', placeholder='10 или 12 цифр', key=f'inn_{version}')

                product = st.selectbox('Продукт', list(PRODUCT_RATES),
                                       format_func=lambda p: f'{p} · демо-ставка {PRODUCT_RATES[p]}%',
                                       key=f'product_{version}')
                a1, a2 = st.columns(2)
                amount = a1.number_input('Сумма, ₽', min_value=100_000, max_value=20_000_000,
                                         value=2_400_000, step=100_000, key=f'amount_{version}')
                term_options = PRODUCT_TERMS.get(product, [6, 12, 18, 24, 36])
                term = a2.selectbox('Срок, месяцев', term_options,
                                    index=min(2, len(term_options) - 1), key=f'term_{version}')

                purpose = st.selectbox('Цель', ['Пополнение оборотных средств', 'Закупка товара к сезону',
                                                'Покупка оборудования', 'Закрытие кассового разрыва'],
                                       key=f'purpose_{version}')
                revenue = st.number_input('Годовая выручка, ₽', min_value=0, value=18_000_000,
                                          step=1_000_000, key=f'revenue_{version}')
                concentration = st.slider('Доля крупнейшего контрагента, %', 0, 100, 35,
                                          key=f'concentration_{version}')

                with st.expander('Демонстрационные условия проверок'):
                    period_mismatch = st.checkbox('Есть показатели за несопоставимые отчётные периоды',
                                                  value=False, key=f'period_mismatch_{version}')
                    missing_bki = st.checkbox('В демо-ответе БКИ не хватает данных', value=False,
                                              key=f'missing_bki_{version}')

                submitted = st.form_submit_button('Создать и отправить заявку', type='primary')

                if submitted:
                    digits = ''.join(ch for ch in inn if ch.isdigit())
                    if not company.strip() or not digits:
                        st.error('Заполните название компании и демо-ИНН.')
                    elif len(digits) not in (10, 12):
                        st.error('ИНН должен содержать 10 цифр (юрлицо) или 12 цифр (ИП).')
                    else:
                        duplicate = inn_has_active_application(digits)
                        if duplicate:
                            st.error(f'По ИНН {digits} уже есть активная заявка '
                                     f'{duplicate["application_id"]} в статусе '
                                     f'«{status_text(duplicate["status"])}». Дождитесь решения по ней.')
                        else:
                            pk = create_application(company.strip(), digits, product, amount, term,
                                                    purpose, revenue, concentration,
                                                    period_mismatch, missing_bki)
                            add_audit(pk, 'client', 'client', 'APPLICATION_SUBMITTED', 'DRAFT', 'SUBMITTED',
                                      'Заявка отправлена клиентом')
                            update_status(pk, 'SUBMITTED', actor='client',
                                          reason='Заявка отправлена клиентом', log=False)
                            saved = get_application(pk)
                            st.session_state['last_created'] = saved['application_id']
                            st.session_state['form_version'] = version + 1
                            st.success(f"Заявка {saved['application_id']} создана. Найдите её по этому ID "
                                       f"в рабочем кабинете банка.")
                            st.rerun()

        with calc_col:
            preview = {
                'amount': amount, 'annual_revenue': revenue, 'term_months': term,
                'product': product, 'concentration_pct': concentration,
                'period_mismatch': False, 'missing_bki': False,
            }
            m = metrics(preview)
            theme.section('Предварительный расчёт', 'Обновляется до отправки заявки.')
            p1, p2 = st.columns(2)
            with p1:
                theme.kpi('Платёж в месяц', fmt_money(m['payment']), f'аннуитет, {m["rate"]}%', accent=True)
            with p2:
                theme.kpi('Всего к возврату', fmt_money(m['total']), f'проценты {fmt_money(m["interest"])}')
            p3, p4 = st.columns(2)
            with p3:
                theme.kpi('Нагрузка на выручку', f'{m["dti"]:.0%}', 'доля годовой выручки')
            with p4:
                theme.kpi('Ориентир по сумме', fmt_money(m['limit']), 'по продукту и выручке')

            if m['dti'] > 0.35:
                st.error(affordability(preview))
            elif m['dti'] > 0.25:
                st.warning(affordability(preview))
            else:
                st.success(affordability(preview))

            if amount > PRODUCT_LIMITS.get(product, 5_000_000):
                st.warning(f'Сумма выше демонстрационного лимита продукта '
                           f'({fmt_money(PRODUCT_LIMITS.get(product, 5_000_000))}).')

            st.caption('График платежей — первые 6 месяцев')
            st.dataframe(payment_schedule(preview, limit=6), hide_index=True, use_container_width=True)


# ─────────────────────────────── кабинет банка ────────────────────────────────
elif mode == 'Рабочий кабинет банка':
    auth_gate('bank', 'Вход в рабочий кабинет банка')
    theme.hero('Рабочее место кредитного аналитика',
               'Очередь заявок, прозрачные сигналы, ИИ-ассистент и фиксация решения с обоснованием.',
               kicker=f'{ROLE_LABELS[role]} · демонстрация')

    if not apps_all:
        st.info('Заявок пока нет. Создайте заявку в клиентском кабинете.')
        st.stop()

    o1, o2, o3, o4 = st.columns(4)
    with o1:
        theme.kpi('В очереди', str(sum(1 for a in apps_all if a['status'] in ACTIVE_STATUSES)), 'активные заявки')
    with o2:
        theme.kpi('На ручном разборе', str(counts.get('MANUAL_REVIEW', 0)), 'нужна экспертиза', accent=True)
    with o3:
        theme.kpi('Ждут клиента', str(counts.get('NEEDS_INFO', 0)), 'отправлен запрос')
    with o4:
        theme.kpi('Средний индекс',
                  f'{sum(score_application(a)["score"] for a in apps_all) / max(len(apps_all), 1):.0f}/100',
                  'по демо-правилам')

    with st.container(border=True):
        f1, f2, f3, f4 = st.columns([2, 1.1, 1.1, 1.2])
        search = f1.text_input('Поиск по ID, компании или ИНН', placeholder='ALF-26-0001 или «Северный»')
        status_filter = f2.selectbox('Статус', ['Все'] + STATUS_OPTIONS,
                                     format_func=lambda s: status_text(s) if s != 'Все' else 'Все')
        sort_by = f3.selectbox('Сортировка', ['Сначала новые', 'Сначала крупные', 'Сначала рискованные'])
        risk_only = f4.checkbox('Только с блокирующими сигналами')

        def matches(a: dict) -> bool:
            if status_filter != 'Все' and a['status'] != status_filter:
                return False
            if search:
                haystack = f"{a['application_id']} {a['company_name']} {a['inn']}".lower()
                if search.lower().strip() not in haystack:
                    return False
            if risk_only and not any(
                    r['severity'] == 'critical' for r in evaluate_application(a)):
                return False
            return True

    filtered = [a for a in apps_all if matches(a)]
    if sort_by == 'Сначала крупные':
        filtered.sort(key=lambda a: -float(a['amount']))
    elif sort_by == 'Сначала рискованные':
        filtered.sort(key=lambda a: score_application(a)['score'])

    st.caption(f'Заявок в выборке: {len(filtered)} из {len(apps_all)}')

    if not filtered:
        st.info('По заданным фильтрам ничего не найдено. Измените поиск или фильтр статуса.')
        st.stop()

    queue_col, card_col = st.columns([1, 2.15], gap='large')

    with queue_col:
        theme.section('Очередь заявок')
        labels = {}
        for a in filtered:
            result = score_application(a)
            labels[f"{a['application_id']} · {a['company_name']} · {result['score']}/100"] = a['id']
        picked = st.radio('Выберите заявку', list(labels), label_visibility='collapsed',
                          key='queue_' + '_'.join(str(a['id']) for a in filtered))
        selected = get_application(labels[picked])

    with card_col:
        if not selected:
            st.stop()

        score = score_application(selected)
        m = metrics(selected)

        app_header(selected)
        st.markdown(theme.stepper(selected['status']), unsafe_allow_html=True)

        g1, g2 = st.columns([0.5, 2.5])
        with g1:
            st.markdown(theme.gauge(score['score'], 'индекс'), unsafe_allow_html=True)
        with g2:
            st.markdown(
                f'<div style="padding-top:8px"><div class="card-title">{theme.esc(score["band"])}</div>'
                f'<div class="card-sub">Блокирующих: {score["critical"]} · предупреждений: '
                f'{score["warnings"]} · без замечаний: {score["passed"]}</div>'
                f'<div class="pill-row">{chip(selected["status"])}'
                f'<span class="chip chip-neutral">Демо-ставка {m["rate"]}%</span>'
                f'<span class="chip chip-neutral">Нагрузка {m["dti"]:.0%}</span></div></div>',
                unsafe_allow_html=True)

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            theme.kpi('Запрошено', fmt_money(selected['amount']), selected['product'], accent=True)
        with k2:
            theme.kpi('Срок', f'{selected["term_months"]} мес.', f'платёж {fmt_money(m["payment"])}')
        with k3:
            theme.kpi('Выручка', theme.short_money(selected['annual_revenue']), 'годовая, заявленная')
        with k4:
            theme.kpi('Концентрация', f'{selected["concentration_pct"]}%', 'крупнейший контрагент')

        tabs = st.tabs(['Обоснование', 'Источники', 'ИИ-агенты', 'ИИ-резюме', 'Действия', 'История'])

        with tabs[0]:
            theme.section('Результат демонстрационных правил',
                          'Правила не описывают кредитную политику банка и не заменяют решение сотрудника.')
            st.markdown(''.join(theme.rule_card(r) for r in score['rules']), unsafe_allow_html=True)
            st.info('Следующий шаг определяется сотрудником: сигналы — материал для проверки, '
                    'а не автоматическое решение.')

            st.markdown('**Чек-лист документов**')
            checklist = document_checklist(selected)
            for item in checklist:
                mark = '🔴 нужен' if item['needed'] else '🟢 не требуется'
                st.markdown(f'- {mark} · **{item["name"]}** — {item["why"]}')

        with tabs[1]:
            theme.section('Демонстрационные ответы источников', 'Реальных подключений в MVP нет.')
            s1, s2 = st.columns(2)
            with s1:
                st.markdown(theme.source_card('ЕГРЮЛ / ЕГРИП', 'ACTIVE', 'Mock-ответ: компания действующая, руководитель совпадает с заявкой.'), unsafe_allow_html=True)
                st.write('')
                st.markdown(theme.source_card(
                    'ФНС', 'Задолженности нет',
                    'Mock-ответ: налоговая задолженность не выявлена в демонстрационных данных.',
                    'ok'), unsafe_allow_html=True)
            with s2:
                st.markdown(theme.source_card(
                    'БКИ', 'Нужны данные' if selected['missing_bki'] else 'score 750',
                    'В mock-ответе недостаточно сведений; запросить подтверждение.' if selected['missing_bki']
                    else 'Активных кредитов: 1 · просрочек: 0 · условный score 750.',
                    'warn' if selected['missing_bki'] else 'ok'), unsafe_allow_html=True)
                st.write('')
                st.markdown(theme.source_card(
                    'Финансовый профиль', 'Введён клиентом',
                    f'Периоды сопоставимы: {"нет" if selected["period_mismatch"] else "да"}. '
                    f'Выручка {theme.short_money(selected["annual_revenue"])}.',
                    'warn' if selected['period_mismatch'] else 'ok'), unsafe_allow_html=True)
            st.caption('Источники синтетические. Для production-версии нужны договоры с бюро и '
                       'официальные API, а также логирование запросов.')

        with tabs[2]:
            theme.section('Команда ИИ-агентов', 'Пять независимых проверок. Решение не принимается автоматически.')
            if st.button('Запустить всех ИИ-агентов', type='primary', key=f'agents_{selected["id"]}'):
                st.session_state[f'agents_result_{selected["id"]}'] = run_agents(selected)
            agent_results = st.session_state.get(f'agents_result_{selected["id"]}')
            if agent_results:
                for row_start in range(0, len(agent_results), 2):
                    cols = st.columns(2)
                    for col, agent in zip(cols, agent_results[row_start:row_start + 2]):
                        with col:
                            st.markdown(theme.agent_card(agent), unsafe_allow_html=True)
                            st.write('')
                st.warning('Итог работы агентов — рекомендация для сотрудника. '
                           'Проверка человеком обязательна.')
            else:
                st.info('Нажмите кнопку, чтобы получить результаты пяти агентов: качество заявки, '
                        'финансовый профиль, структура выручки, источники и рекомендации аналитику.')

        with tabs[3]:
            theme.section('ИИ-резюме по заявке', 'Резюме строится только на переданных полях.')
            r1, r2 = st.columns([1, 1])
            with r1:
                if st.button('Сформировать ИИ-резюме', key=f'ai_{selected["id"]}', type='primary'):
                    st.session_state[f'ai_result_{selected["id"]}'] = analyze_application(selected)
            with r2:
                if st.button('Черновик запроса клиенту', key=f'draft_{selected["id"]}'):
                    st.session_state[f'draft_{selected["id"]}'] = client_message_draft(selected)

            ai_result = st.session_state.get(f'ai_result_{selected["id"]}')
            if ai_result:
                st.markdown(f'<div class="agent"><div class="sm" style="margin-top:0">{theme.esc(ai_result["mode"])}</div>'
                            f'<div style="font-size:.9rem;line-height:1.55">{ai_result["summary"]}</div></div>',
                            unsafe_allow_html=True)
                st.markdown('**Сигналы**')
                for signal in ai_result['signals']:
                    st.markdown(f'- {signal}')
                st.markdown('**Что сделать дальше**')
                for action in ai_result.get('next_actions', []):
                    st.markdown(f'- {action}')
            else:
                st.write('ИИ подготовит краткое резюме и список сигналов для сотрудника. '
                         'Сигнал ИИ не меняет статус и не является решением по кредиту.')

            draft = st.session_state.get(f'draft_{selected["id"]}')
            if draft:
                st.markdown('**Черновик сообщения клиенту**')
                st.text_area('Можно скопировать в запрос уточнения', draft, height=170,
                             key=f'draft_text_{selected["id"]}')

        with tabs[4]:
            theme.section('Действия по заявке', f'Текущая роль: {ROLE_LABELS[role]}')
            can_check = selected['status'] in ('SUBMITTED', 'VALIDATION', 'ENRICHMENT', 'SCORING', 'NEEDS_INFO')
            if can_check:
                if st.button('Запустить демо-проверки', type='primary', key=f'check_{selected["id"]}'):
                    current = selected['status']
                    if current == 'SUBMITTED':
                        update_status(selected['id'], 'VALIDATION', actor=role,
                                      reason='Начата формальная проверка')
                        current = 'VALIDATION'
                    if current in ('VALIDATION', 'ENRICHMENT', 'NEEDS_INFO'):
                        update_status(selected['id'], 'ENRICHMENT', actor=role,
                                      reason='Формальная проверка завершена; запрос демо-источников')
                        current = 'ENRICHMENT'
                    if current == 'ENRICHMENT':
                        update_status(selected['id'], 'SCORING', actor=role,
                                      reason='Демо-источники получены; расчёт показателей')
                        current = 'SCORING'
                    if selected['missing_bki']:
                        update_status(
                            selected['id'], 'NEEDS_INFO', actor=role,
                            reason='В демо-ответе БКИ недостаточно данных',
                            client_message=('Пожалуйста, приложите справку об отсутствии задолженности '
                                            'или пояснение по кредитной истории.'))
                    elif selected['period_mismatch'] or selected['concentration_pct'] >= 50:
                        update_status(selected['id'], 'MANUAL_REVIEW', actor=role,
                                      reason='Требуется экспертная проверка финансовых данных')
                    else:
                        st.session_state[f'check_passed_{selected["id"]}'] = True
                        update_status(selected['id'], 'MANUAL_REVIEW', actor=role,
                                      reason='Демо-проверки пройдены; ожидает решения сотрудника')
                    st.rerun()

            if role in ('analyst', 'senior_analyst', 'admin'):
                if 'NEEDS_INFO' not in ALLOWED_TRANSITIONS.get(selected['status'], ()):
                    st.caption(f'Из статуса «{status_text(selected["status"])}» запросить уточнения нельзя — '
                               'сначала завершите текущий этап проверки.')
                else:
                    with st.form(f'request_info_{selected["id"]}'):
                        default_msg = client_message_draft(selected)
                        message = st.text_area('Запрос уточнения клиенту', value=default_msg, height=140)
                        if st.form_submit_button('Отправить запрос клиенту'):
                            if len(message.strip()) < 10:
                                st.error('Сформулируйте запрос подробнее (минимум 10 символов).')
                            else:
                                add_audit(selected['id'], 'bank', role, 'INFO_REQUESTED',
                                          selected['status'], 'NEEDS_INFO', message.strip())
                                update_status(selected['id'], 'NEEDS_INFO', actor=role,
                                              reason='Запрошены дополнительные сведения',
                                              client_message=message.strip(), log=False)
                                st.rerun()
            else:
                st.caption('Запрос уточнений доступен аналитику и старшему аналитику.')

            if role in ('senior_analyst', 'admin'):
                if selected['status'] == 'MANUAL_REVIEW':
                    with st.form(f'decision_{selected["id"]}'):
                        st.markdown('**Зафиксировать результат**')
                        decision = st.selectbox(
                            'Демо-результат',
                            ['APPROVED', 'CONDITIONAL_APPROVAL', 'NEEDS_INFO', 'REJECTED'],
                            format_func=status_text)
                        terms_default = (f'Сумма {fmt_money(min(float(selected["amount"]), m["limit"]))}, '
                                         f'срок {selected["term_months"]} мес., ставка {m["rate"]}%')
                        terms = st.text_input('Условия (для одобрения)', value=terms_default)
                        reason = st.text_area('Основание решения', height=110,
                                              placeholder='Какие факты проверены и на что опирается решение')
                        if st.form_submit_button('Зафиксировать результат', type='primary'):
                            if len(reason.strip()) < 15:
                                st.error('Основание решения должно содержать минимум 15 символов.')
                            else:
                                set_decision(selected['id'], decision, terms, reason.strip(), role)
                                update_status(selected['id'], decision, actor=role, reason=reason.strip())
                                st.rerun()
                else:
                    st.caption('Финальное решение принимается на этапе «На ручном разборе». '
                               f'Текущий статус: «{status_text(selected["status"])}».')
            else:
                st.caption('Финальное решение доступно старшему аналитику и администратору.')

            # Дальше сделку ведёт клиент: принятие предложения, подписание, получение денег.
            if selected['status'] in OFFER_STATUSES:
                st.info('Решение зафиксировано. Клиент видит предложение в своём кабинете '
                        'и может принять его, отклонить или запросить изменения.')
            elif selected['status'] == 'ACCEPTED':
                st.info('Клиент принял предложение. Идёт подписание демо-кодом (имитация ЭП).')
            elif selected['status'] == 'SIGNING':
                st.info('Клиент подписывает кредитное предложение кодом.')
            elif selected['status'] == 'SIGNED':
                st.success('Кредитное предложение подписано. Осталась смоделированная выдача.')
                if st.button('Провести смоделированную выдачу', key=f'disb_{selected["id"]}',
                             type='primary', use_container_width=True):
                    disburse(selected['id'])
                    st.rerun()
            elif selected['status'] == 'DISBURSED':
                d_rec = get_disbursement(selected['id'])
                if d_rec:
                    st.success(f'✅ Сделка завершена: смоделированная выдача на демо-счёт '
                               f'{d_rec["account_number"]} · {theme.money(d_rec["amount"])}. '
                               f'Посткредитное обслуживание не входит в MVP.')

        with tabs[5]:
            theme.section('Журнал аудита', 'Каждое изменение статуса фиксируется с ролью и основанием.')
            st.markdown(theme.timeline(get_audit(selected['id'])), unsafe_allow_html=True)
            docs = get_documents(selected['id'])
            if docs:
                st.markdown('**Документы**')
                st.dataframe(
                    pd.DataFrame([{'Файл': d['filename'], 'Тип': d.get('content_type') or '—',
                                   'Размер, КБ': round((d.get('size_bytes') or 0) / 1024, 1),
                                   'Загружен': d['uploaded_at']} for d in docs]),
                    hide_index=True, use_container_width=True)
            else:
                st.caption('Документы не загружены.')


# ─────────────────────────────── аналитика портфеля ───────────────────────────
else:
    theme.hero('Аналитика портфеля заявок',
               'Сводка по демонстрационному портфелю: объём, распределение и качество заявок.',
               kicker='Внутренняя аналитика · демонстрация')

    if not apps_all:
        st.info('Нет данных для аналитики.')
        st.stop()

    frame = pd.DataFrame(apps_all)
    frame['amount'] = frame['amount'].astype(float)
    frame['score'] = [score_application(a)['score'] for a in apps_all]
    frame['status_label'] = frame['status'].map(lambda s: status_text(s))
    decided = frame[frame['status'].isin(['APPROVED', 'CONDITIONAL_APPROVAL', 'ACCEPTED', 'SIGNING', 'SIGNED', 'DISBURSED'])]

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        theme.kpi('Объём портфеля', theme.short_money(frame['amount'].sum()), f'{len(frame)} заявок', accent=True)
    with a2:
        theme.kpi('Средняя сумма', theme.short_money(frame['amount'].mean()), 'по всем заявкам')
    with a3:
        theme.kpi('Средний индекс качества', f'{frame["score"].mean():.0f}/100', 'по 8 правилам')
    with a4:
        theme.kpi('Одобрено', f'{len(decided)}', f'{len(decided) / len(frame):.0%} от портфеля')

    left, right = st.columns(2, gap='large')
    with left:
        with st.container(border=True):
            theme.section('Сумма по статусам, ₽')
            by_status = (frame.groupby('status_label', as_index=False)['amount'].sum()
                         .sort_values('amount', ascending=False).set_index('status_label'))
            st.bar_chart(by_status, height=280, color='#EF3124')
    with right:
        with st.container(border=True):
            theme.section('Сумма по продуктам, ₽')
            by_product = (frame.groupby('product', as_index=False)['amount'].sum()
                          .set_index('product'))
            st.bar_chart(by_product, height=280, color='#C61B12')

    with st.container(border=True):
        theme.section('Все заявки', 'Сортировка по индексу качества: сверху — требующие внимания.')
        table = frame.assign(
            Заявка=frame['application_id'],
            Компания=frame['company_name'],
            Продукт=frame['product'],
            Сумма=frame['amount'].map(lambda v: theme.money(v)),
            Срок=frame['term_months'].map(lambda v: f'{v} мес.'),
            Статус=frame['status_label'],
            Индекс=frame['score'],
            Концентрация=frame['concentration_pct'].map(lambda v: f'{v}%'),
        )[['Заявка', 'Компания', 'Продукт', 'Сумма', 'Срок', 'Статус', 'Индекс', 'Концентрация']]
        st.dataframe(table.sort_values('Индекс'), hide_index=True, use_container_width=True)

st.divider()
st.caption('Демонстрационный прототип. Не использовать для реального кредитования, '
           'а также для загрузки персональных и конфиденциальных данных. '
           'Визуальный стиль — стилизация, товарные знаки Альфа-Банка не используются.')
