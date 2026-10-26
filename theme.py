"""Визуальный слой: фирменная палитра, типографика и переиспользуемые компоненты.

Палитра и типографика воспроизводят визуальный язык Альфа-Банка
(«альфа-красный» #EF3124, плотная гротескная гарнитура, много воздуха,
крупные радиусы скругления). Логотип банка не используется — только
нейтральная литера «А» как плейсхолдер.
"""
from __future__ import annotations

import html

import streamlit as st

BRAND = '#EF3124'

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700;800&display=swap');

:root{
  --alfa:#EF3124; --alfa-2:#C61B12; --alfa-soft:#FFF2F0; --alfa-line:#FFD6D1;
  --ink:#0E0E0E; --ink2:#3D3D3D; --muted:#7C7C7C; --line:#E7E7E7; --soft:#F7F7F7;
  --ok:#0B6E4F; --ok-bg:#E8F6EF; --warn:#9A5B00; --warn-bg:#FFF4E2;
  --risk:#B3180D; --risk-bg:#FFECEA;
}

html, body, .stApp{
  font-family:'Inter Tight','Styrene A','Alfa Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
}
.stApp :is(p,h1,h2,h3,h4,h5,h6,label,li,td,th,button,input,textarea,div[data-testid="stMarkdownContainer"]){
  font-family:'Inter Tight','Styrene A','Alfa Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
}

.stApp{background:#fff; color:var(--ink);}
.block-container{padding-top:1.1rem; padding-bottom:3rem; max-width:1520px;}
[data-testid="stHeader"]{background:transparent; height:0;}
[data-testid="stToolbar"]{right:.4rem; top:.2rem;}
footer{visibility:hidden;}

h1,h2,h3,h4{color:var(--ink); letter-spacing:-.02em; font-weight:800;}
h1{font-size:1.9rem;} h2{font-size:1.35rem;} h3{font-size:1.08rem;}
p, li{color:var(--ink2);}
hr{border-color:var(--line);}
[data-testid="stCaptionContainer"], .stCaption{color:var(--muted) !important;}

/* ---------- шапка-герой ---------- */
.ab-hero{
  position:relative; overflow:hidden; color:#fff; border-radius:24px;
  padding:24px 30px 22px; margin:4px 0 20px;
  background:radial-gradient(1200px 340px at 100% -10%, #FF6A55 0%, #EF3124 42%, #D31F14 100%);
  box-shadow:0 18px 40px -24px rgba(239,49,36,.75);
}
.ab-hero:after{
  content:""; position:absolute; right:-80px; bottom:-140px; width:320px; height:320px;
  border-radius:50%; background:rgba(255,255,255,.08);
}
.ab-hero .top{display:flex; align-items:center; gap:10px;}
.ab-hero .mark{
  width:34px; height:34px; border-radius:11px; background:#fff; color:var(--alfa);
  display:grid; place-items:center; font-weight:800; font-size:1.05rem;
}
.ab-hero .kicker{
  font-size:.7rem; letter-spacing:.14em; text-transform:uppercase;
  font-weight:700; color:rgba(255,255,255,.85);
}
.ab-hero h1{
  color:#fff; font-size:1.95rem; line-height:1.15; margin:14px 0 6px; font-weight:800;
}
.ab-hero p{margin:0; color:rgba(255,255,255,.88); font-size:.93rem; max-width:820px;}

/* ---------- KPI ---------- */
.ab-kpi{
  background:#fff; border:1px solid var(--line); border-radius:18px;
  padding:14px 16px; height:100%;
}
.ab-kpi .lbl{
  font-size:.7rem; letter-spacing:.08em; text-transform:uppercase;
  color:var(--muted); font-weight:700;
}
.ab-kpi .val{
  font-size:1.42rem; font-weight:800; letter-spacing:-.02em; margin-top:5px; color:var(--ink);
  line-height:1.15;
}
.ab-kpi .hint{font-size:.76rem; color:var(--muted); margin-top:3px;}
.ab-kpi.accent{border-color:var(--alfa-line); background:linear-gradient(180deg,#fff 0%,#FFF7F6 100%);}
.ab-kpi.accent .val{color:var(--alfa);}

/* ---------- чипы статусов ---------- */
.chip{
  display:inline-block; padding:5px 11px; border-radius:999px;
  font-size:.75rem; font-weight:700; white-space:nowrap; letter-spacing:-.01em;
}
.chip-ok{background:var(--ok-bg); color:var(--ok);}
.chip-warn{background:var(--warn-bg); color:var(--warn);}
.chip-risk{background:var(--risk-bg); color:var(--risk);}
.chip-info{background:#EFEFF3; color:#43434D;}
.chip-neutral{background:#F3F3F3; color:#6B6B6B;}
.chip-alfa{background:var(--alfa); color:#fff;}

/* ---------- карточки ---------- */
.card-title{font-size:1.1rem; font-weight:800; letter-spacing:-.01em;}
.card-sub{font-size:.8rem; color:var(--muted); margin-top:2px;}
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:18px;}
[data-testid="stVerticalBlockBorderWrapper"] > div{border-color:var(--line) !important;}
[data-testid="stMetric"]{background:#fff; border:1px solid var(--line); border-radius:14px; padding:12px 14px;}
[data-testid="stMetricLabel"]{color:var(--muted);}
[data-testid="stMetricValue"]{font-size:1.25rem; font-weight:800; color:var(--ink);}
[data-testid="stMetricDelta"]{font-size:.75rem;}

/* ---------- прогресс-шаги ---------- */
.steps{display:flex; gap:6px; margin:16px 0 6px;}
.step{flex:1;}
.step .bar{height:5px; border-radius:999px; background:#EDEDED;}
.step.done .bar{background:var(--alfa);}
.step.current .bar{background:var(--alfa); opacity:.5;}
.step.alert .bar{background:#F5A623;}
.step .lbl{font-size:.68rem; color:var(--muted); margin-top:6px; letter-spacing:-.01em;}
.step.done .lbl, .step.current .lbl, .step.alert .lbl{color:var(--ink); font-weight:600;}

/* ---------- индикатор качества ---------- */
.gauge{
  width:116px; height:116px; border-radius:50%; display:grid; place-items:center;
  background:conic-gradient(var(--c) calc(var(--p) * 1%), #EFEFEF 0);
}
.gauge .in{width:90px; height:90px; border-radius:50%; background:#fff; display:grid; place-items:center; text-align:center;}
.gauge .num{font-size:1.5rem; font-weight:800; line-height:1; color:var(--ink);}
.gauge .cap{font-size:.58rem; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; margin-top:3px;}

/* ---------- правила ---------- */
.rule{
  border:1px solid var(--line); border-left:4px solid #DDD; border-radius:12px;
  padding:11px 14px; margin-bottom:8px; background:#fff;
}
.rule.critical{border-left-color:#E0180B; background:#FFFAF9;}
.rule.warning{border-left-color:#F5A623; background:#FFFDF7;}
.rule.ok{border-left-color:#15A36A;}
.rule .rid{font-size:.66rem; letter-spacing:.09em; color:var(--muted); font-weight:700;}
.rule .ttl{font-weight:700; font-size:.93rem; margin:2px 0 3px; color:var(--ink);}
.rule .exp{font-size:.85rem; color:var(--ink2); line-height:1.45;}

/* ---------- агенты / источники ---------- */
.agent{border:1px solid var(--line); border-radius:14px; padding:14px 16px; background:#fff; height:100%;}
.agent .num{
  width:26px; height:26px; border-radius:8px; background:var(--alfa-soft); color:var(--alfa);
  display:inline-grid; place-items:center; font-size:.72rem; font-weight:800; margin-right:8px;
}
.agent .nm{font-weight:800; font-size:.95rem; color:var(--ink);}
.agent .sm{font-size:.8rem; color:var(--muted); margin:4px 0 8px;}
.agent ul{margin:0 0 8px 0; padding-left:16px;}
.agent li{font-size:.84rem; margin-bottom:3px; color:var(--ink2);}
.agent .nx{font-size:.8rem; color:var(--ink); background:var(--soft); border-radius:10px; padding:8px 10px;}

.src{border:1px solid var(--line); border-radius:14px; padding:13px 15px; background:#fff; height:100%;}
.src .nm{font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:700;}
.src .vl{font-size:.95rem; font-weight:700; margin:6px 0 4px; color:var(--ink);}
.src .ds{font-size:.79rem; color:var(--ink2); line-height:1.4;}

/* ---------- таймлайн ---------- */
.tl{list-style:none; padding:0; margin:10px 0 0;}
.tl li{position:relative; padding:0 0 13px 20px; margin-left:5px; border-left:1px solid var(--line);}
.tl li:last-child{border-left-color:transparent; padding-bottom:0;}
.tl li:before{
  content:""; position:absolute; left:-5px; top:3px; width:9px; height:9px;
  border-radius:50%; background:var(--alfa); box-shadow:0 0 0 3px #fff;
}
.tl .ts{font-size:.7rem; color:var(--muted);}
.tl .ac{font-size:.86rem; font-weight:600; color:var(--ink);}
.tl .rs{font-size:.79rem; color:var(--ink2);}

/* ---------- элементы управления ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button{
  border-radius:12px; font-weight:600; border:1px solid #DEDEDE; color:var(--ink);
  padding:.45rem .9rem;
}
.stButton > button:hover, .stDownloadButton > button:hover{border-color:var(--alfa); color:var(--alfa);}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"], .stDownloadButton > button[kind="primary"]{
  background:var(--alfa); border:1px solid var(--alfa); color:#fff;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover{
  background:var(--alfa-2); border-color:var(--alfa-2); color:#fff;
}
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div{
  border-radius:12px !important;
}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input{font-weight:500;}
[data-baseweb="tab-list"]{gap:6px; border-bottom:1px solid var(--line);}
[data-baseweb="tab"]{font-weight:600; color:var(--muted); padding-left:2px; padding-right:2px; margin-right:14px;}
[data-baseweb="tab"][aria-selected="true"]{color:var(--ink);}
[data-baseweb="tab-highlight"]{background:var(--alfa); height:2px;}
[data-testid="stExpander"] details{border-radius:14px; border-color:var(--line);}
[data-testid="stExpander"] summary{font-weight:600;}
[data-testid="stSidebar"]{background:#FCFCFC; border-right:1px solid var(--line);}
[data-testid="stSidebar"] .block-container{padding-top:1.2rem;}
[data-testid="stSidebar"] div[role="radiogroup"]{gap:3px;}
[data-testid="stSidebar"] div[role="radiogroup"] label{
  padding:7px 10px; border-radius:12px; transition:background .15s ease; width:100%;
}
[data-testid="stSidebar"] div[role="radiogroup"] label:hover{background:#F2F2F2;}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked){background:var(--alfa-soft);}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p{font-weight:700; color:var(--alfa-2);}
[data-testid="stDataFrame"]{border-radius:14px; overflow:hidden; border:1px solid var(--line);}
.side-brand{display:flex; align-items:center; gap:9px; margin-bottom:14px;}
.side-brand .mark{width:30px; height:30px; border-radius:9px; background:var(--alfa); color:#fff;
  display:grid; place-items:center; font-weight:800; font-size:.95rem;}
.side-brand .tx{font-weight:800; font-size:.92rem; line-height:1.1; letter-spacing:-.01em;}
.side-brand .tx small{display:block; font-weight:500; font-size:.68rem; color:var(--muted);}
.side-stat{border:1px solid var(--line); border-radius:14px; padding:11px 13px; background:#fff; margin:4px 0 10px;}
.side-stat .r{display:flex; justify-content:space-between; font-size:.79rem; color:var(--ink2); padding:2px 0;}
.side-stat .r b{color:var(--ink);}
.note{font-size:.78rem; color:var(--muted); line-height:1.45;}
.pill-row{display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;}
</style>
"""


def inject_theme() -> None:
    """Подключает фирменные стили. Вызывается один раз после set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ''))


def money(value, suffix: str = ' ₽') -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '—'
    return f'{number:,.0f}{suffix}'.replace(',', ' ')


def short_money(value) -> str:
    """1 250 000 ₽ -> 1,25 млн ₽ — для компактных подписей."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '—'
    if abs(number) >= 1_000_000:
        return f'{number / 1_000_000:.2f} млн ₽'.replace('.', ',')
    if abs(number) >= 1_000:
        return f'{number / 1_000:.0f} тыс. ₽'
    return money(number)


def hero(title: str, subtitle: str, kicker: str = 'Альфа-Бизнес · демонстрация') -> None:
    st.markdown(
        f"""
        <div class="ab-hero">
          <div class="top"><span class="mark">А</span><span class="kicker">{esc(kicker)}</span></div>
          <h1>{esc(title)}</h1>
          <p>{esc(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi(label: str, value: str, hint: str = '', accent: bool = False) -> None:
    cls = 'ab-kpi accent' if accent else 'ab-kpi'
    hint_html = f'<div class="hint">{esc(hint)}</div>' if hint else ''
    st.markdown(
        f'<div class="{cls}"><div class="lbl">{esc(label)}</div>'
        f'<div class="val">{esc(value)}</div>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def section(title: str, hint: str = '') -> None:
    hint_html = f'<div class="note">{esc(hint)}</div>' if hint else ''
    st.markdown(
        f'<div style="margin:14px 0 8px"><div class="card-title">{esc(title)}</div>{hint_html}</div>',
        unsafe_allow_html=True,
    )


def status_chip(status: str) -> str:
    from rules import STATUS_LABELS, STATUS_TONE

    label = STATUS_LABELS.get(status, status)
    tone = STATUS_TONE.get(status, 'neutral')
    return f'<span class="chip chip-{tone}">{esc(label)}</span>'


STEPS = [
    ('SUBMITTED', 'Заявка'),
    ('VALIDATION', 'Проверка'),
    ('ENRICHMENT', 'Данные'),
    ('SCORING', 'Скоринг'),
    ('DECISION', 'Решение'),
    ('ACCEPTED', 'Принято'),
    ('SIGNING', 'Подписание'),
    ('DISBURSED', 'Выдано'),
]

_STEP_ALIAS = {
    'DRAFT': 'SUBMITTED',
    'NEEDS_INFO': 'ENRICHMENT',
    'MANUAL_REVIEW': 'DECISION',
    'APPROVED': 'DECISION',
    'CONDITIONAL_APPROVAL': 'DECISION',
    'REJECTED': 'DECISION',
    'SIGNED': 'SIGNING',
}


def stepper(status: str) -> str:
    key = _STEP_ALIAS.get(status, status)
    keys = [s[0] for s in STEPS]
    idx = keys.index(key) if key in keys else 0
    alert = status in ('NEEDS_INFO', 'MANUAL_REVIEW', 'REJECTED')
    parts = ['<div class="steps">']
    for i, (_, label) in enumerate(STEPS):
        if i < idx:
            cls = 'done'
        elif i == idx:
            cls = 'alert' if alert else 'current'
        else:
            cls = 'todo'
        parts.append(f'<div class="step {cls}"><div class="bar"></div><div class="lbl">{esc(label)}</div></div>')
    parts.append('</div>')
    return ''.join(parts)


def gauge(score: int, caption: str = 'качество', color: str = '#EF3124') -> str:
    score = max(0, min(100, int(score)))
    return (
        f'<div class="gauge" style="--p:{score};--c:{color}">'
        f'<div class="in"><div><div class="num">{score}</div><div class="cap">{esc(caption)}</div></div></div></div>'
    )


def rule_card(rule: dict) -> str:
    severity = rule.get('severity', 'ok')
    return (
        f'<div class="rule {severity}">'
        f'<div class="rid">{esc(rule.get("rule_id", ""))}</div>'
        f'<div class="ttl">{esc(rule.get("title", ""))}</div>'
        f'<div class="exp">{esc(rule.get("explanation", ""))}</div>'
        f'</div>'
    )


def agent_card(agent: dict) -> str:
    tone = agent.get('tone', 'info')
    findings = ''.join(f'<li>{esc(f)}</li>' for f in agent.get('findings', []))
    return (
        f'<div class="agent">'
        f'<div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">'
        f'<div><span class="num">{esc(agent.get("icon", ""))}</span><span class="nm">{esc(agent.get("name", ""))}</span></div>'
        f'<span class="chip chip-{tone}">{esc(agent.get("status", ""))}</span></div>'
        f'<div class="sm">{esc(agent.get("summary", ""))}</div>'
        f'<ul>{findings}</ul>'
        f'<div class="nx"><b>Следующий шаг:</b> {esc(agent.get("next_step", ""))}</div>'
        f'</div>'
    )


def source_card(name: str, value: str, details: str, tone: str = 'ok') -> str:
    chip = {'ok': 'chip-ok', 'warn': 'chip-warn', 'risk': 'chip-risk'}.get(tone, 'chip-info')
    return (
        f'<div class="src"><div class="nm">{esc(name)}</div>'
        f'<div class="vl"><span class="{chip}" style="font-size:.72rem">{esc(value)}</span></div>'
        f'<div class="ds">{esc(details)}</div></div>'
    )


def timeline(events) -> str:
    items = []
    for event in events:
        items.append(
            f'<li><div class="ts">{esc(event.get("timestamp", ""))} · {esc(event.get("actor_role", ""))}</div>'
            f'<div class="ac">{esc(event.get("action", ""))}</div>'
            f'<div class="rs">{esc(event.get("reason") or "—")}</div></li>'
        )
    return '<ul class="tl">' + ''.join(items) + '</ul>'


def sidebar_brand(title: str = 'Кредитный кабинет', subtitle: str = 'демо · синтетические данные') -> str:
    return (
        f'<div class="side-brand"><span class="mark">А</span>'
        f'<span class="tx">{esc(title)}<small>{esc(subtitle)}</small></span></div>'
    )
