"""Финансовые расчёты демо-модели: аннуитет, график платежей, долговая нагрузка.

Все ставки и лимиты — демонстрационные. Базовая вилка ставок привязана к текущей
ключевой ставке ЦБ РФ (14% годовых, действует с 27.07.2026, источник — cbr.ru):
ставка продукта = ключевая ставка + премия за риск/срок (2,4–5,9 п.п.).
Они не описывают продукты и политики Альфа-Банка и нужны только для
наглядности расчётов в прототипе.
"""
from __future__ import annotations

import pandas as pd

# Ключевая ставка ЦБ РФ, % (справочно, для обоснования демо-ставок)
KEY_RATE_PCT = 14.0
KEY_RATE_SOURCE = 'Банк России, cbr.ru — ключевая ставка 14,00% с 27.07.2026'

PRODUCT_RATES = {
    'Оборотное финансирование': 16.9,      # КС + 2,9 п.п.
    'Кредит на развитие бизнеса': 19.9,    # КС + 5,9 п.п.
    'Краткосрочное финансирование': 16.4,  # КС + 2,4 п.п.
}

PRODUCT_TERMS = {
    'Оборотное финансирование': [6, 12, 18, 24, 36],
    'Кредит на развитие бизнеса': [12, 18, 24, 36],
    'Краткосрочное финансирование': [3, 6, 9, 12],
}

# Демонстрационные лимиты по продукту, ₽
PRODUCT_LIMITS = {
    'Оборотное финансирование': 8_000_000,
    'Кредит на развитие бизнеса': 12_000_000,
    'Краткосрочное финансирование': 3_000_000,
}

DEFAULT_RATE = 17.5


def annuity_payment(amount: float, rate_pct: float, months: int) -> float:
    """Ежемесячный аннуитетный платёж."""
    amount = float(amount or 0)
    months = int(months or 0)
    if amount <= 0 or months <= 0:
        return 0.0
    monthly = float(rate_pct) / 100 / 12
    if monthly <= 0:
        return amount / months
    return amount * monthly / (1 - (1 + monthly) ** (-months))


def metrics(app: dict) -> dict:
    """Ключевые показатели заявки для интерфейса и правил."""
    amount = float(app.get('amount') or 0)
    revenue = float(app.get('annual_revenue') or 0)
    term = int(app.get('term_months') or 0)
    product = app.get('product') or ''
    rate = PRODUCT_RATES.get(product, DEFAULT_RATE)

    payment = annuity_payment(amount, rate, term)
    total = payment * term
    interest = max(total - amount, 0.0)
    annual_service = payment * 12
    dti = annual_service / revenue if revenue > 0 else 0.0
    coverage = revenue / annual_service if annual_service > 0 else 0.0
    base_limit = PRODUCT_LIMITS.get(product, 5_000_000)
    limit = min(base_limit, revenue * 0.3) if revenue > 0 else base_limit

    return {
        'amount': amount,
        'revenue': revenue,
        'term': term,
        'rate': rate,
        'payment': payment,
        'total': total,
        'interest': interest,
        'annual_service': annual_service,
        'dti': dti,
        'coverage': coverage,
        'limit': limit,
        'overpayment_share': (interest / amount) if amount else 0.0,
    }


def payment_schedule(app: dict, limit: int | None = None) -> pd.DataFrame:
    """График погашения по месяцам (не более `limit` строк, по умолчанию — весь срок)."""
    m = metrics(app)
    amount, term, rate = m['amount'], m['term'], m['rate']
    limit = term if limit is None else min(limit, term)
    rows = []
    balance = amount
    monthly = rate / 100 / 12
    payment = m['payment']
    for month in range(1, max(limit, 0) + 1):
        interest = balance * monthly
        principal = min(payment - interest, balance)
        balance = max(balance - principal, 0.0)
        rows.append({
            'Месяц': month,
            'Платёж, ₽': round(payment, 0),
            'Проценты, ₽': round(interest, 0),
            'Основной долг, ₽': round(principal, 0),
            'Остаток, ₽': round(balance, 0),
        })
    return pd.DataFrame(rows)


def affordability(app: dict) -> str:
    """Короткая текстовая оценка долговой нагрузки для подсказки в форме."""
    m = metrics(app)
    if m['revenue'] <= 0:
        return 'Выручка не указана — расчёт нагрузки недоступен.'
    dti = m['dti']
    if dti <= 0.15:
        return 'Нагрузка низкая: обслуживание долга занимает менее 15% годовой выручки.'
    if dti <= 0.30:
        return 'Нагрузка умеренная: от 15% до 30% годовой выручки.'
    return 'Нагрузка высокая: более 30% годовой выручки уходит на обслуживание долга.'
