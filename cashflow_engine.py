"""Прогноз остатка по дням и поиск кассовых разрывов.

build_forecast: остаток = старт + поток от forecaster + известные события (регулярные, налоги, ручные).
find_gaps: первый день, где вероятность уйти в минус >= GAP_PROB, -> предупреждение с причинами и советами.
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

from categories import *

GAP_PROB = 0.4         # порог вероятности разрыва (на бэктесте лучший F1)
REASON_DAYS = 5        # причины ищем среди платежей за 5 дней до разрыва
MAX_REASONS = 3
MIN_REASON_SHARE = 0.05  # платёж меньше 5% от нехватки - не причина (комиссия 990 ₽ при разрыве в 2 млн)


def build_forecast(start_balance, start_date, flows, events):
    """-> date, balance, low, high, prob (вероятность, что в этот день остаток < 0)"""
    flows = flows.reset_index(drop=True)
    forecast = flows[['date']].copy()
    by_day = events.groupby(pd.to_datetime(events['date']))['amount'].sum()
    forecast['balance'] = start_balance + (flows['flow'] + forecast['date'].map(by_day).fillna(0)).cumsum()
    # неопределённость растёт со временем как sigma * корень из номера дня
    spread = flows['sigma'] * np.sqrt(np.arange(1, len(forecast) + 1))
    forecast['low'] = forecast['balance'] - 1.65 * spread
    forecast['high'] = forecast['balance'] + 1.65 * spread
    # вероятность, что в этот день остаток будет ниже нуля
    forecast['prob'] = norm.cdf(-forecast['balance'] / (spread + 1))
    return forecast


def money(amount):
    """214 300 -> '214 тыс.', 1 350 000 -> '1,35 млн'"""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f'{amount / 1_000_000:.2f}'.rstrip('0').rstrip('.').replace('.', ',') + ' млн'
    if amount >= 1_000:
        return f'{amount / 1_000:.0f} тыс.'
    return f'{amount:.0f} ₽'


def _reasons(date, events, shortfall, only_spread, already_negative):
    """Почему возникает разрыв: крупные платежи за REASON_DAYS дней до него (включая сам день).

    Мелкие платежи (меньше 5% от нехватки) не показываем - это не причина.
    """
    payments = []
    if not events.empty:
        dates = pd.to_datetime(events['date'])
        near = events[(events['amount'] < 0)
                      & (dates > date - pd.Timedelta(days=REASON_DAYS)) & (dates <= date)
                      & (events['amount'].abs() >= MIN_REASON_SHARE * shortfall)]
        near = near.assign(date=pd.to_datetime(near['date'])).sort_values('amount').head(MAX_REASONS)
        payments = [f"{row['name']} {row['date']:%d.%m} — {money(row['amount'])}" for _, row in near.iterrows()]

    reasons = []
    if already_negative:
        reasons.append('Остаток уже отрицательный: счёт, похоже, в овердрафте')
    reasons += payments
    if only_spread:
        reasons.append('Остаток близок к нулю: обычные колебания поступлений могут увести его в минус')
    elif not payments:
        reasons.append('Крупных обязательных платежей рядом нет: остаток снижается из-за текущих расходов '
                       '(закупки, услуги), поступлений не хватает')
    return reasons


def _advice(date, days_left, shortfall, has_mandatory, only_spread):
    """2-3 совета: что сделать, чтобы разрыва не было."""
    after = f'{date + pd.Timedelta(days=7):%d.%m}'
    suppliers = f'Договоритесь с поставщиками о переносе оплат на {after} — это освободит деньги без штрафов'
    if has_mandatory:
        suppliers += ' (зарплату и налоги переносить нельзя: пени и штрафы)'
    clients = (f'Напомните клиентам об оплате выставленных счетов до {date:%d.%m} '
               'или предложите скидку за досрочную оплату')
    if only_spread:
        reserve = (f'Держите резерв около {money(shortfall)} и не делайте крупных необязательных '
                   f'платежей до {date:%d.%m}')
        return [reserve, clients, suppliers]
    overdraft = f'Подключите овердрафт или кредитную линию минимум на {money(shortfall)}'
    if days_left <= 3:
        # времени мало: договориться с клиентами и поставщиками можно не успеть
        return [overdraft + ' — до разрыва меньше 4 дней', suppliers, clients]
    return [suppliers, clients, overdraft + ', если первые два шага не помогут']


def find_gaps(forecast, events):
    """-> список предупреждений (пустой или из одного): date, shortfall, probability, reasons, advice

    date        - первый день, где вероятность уйти в минус >= GAP_PROB
    shortfall   - сколько не хватит: самый глубокий минус с этого дня; если в среднем остаток
                  не отрицательный и риск только из-за разброса - минимум нижней границы (-low)
    probability - максимальная вероятность минуса за 30 дней
    """
    if forecast.empty:
        return []
    risky = forecast[forecast['prob'] >= GAP_PROB]
    if risky.empty:
        return []

    date = risky['date'].iloc[0]
    after = forecast[forecast['date'] >= date]
    deepest = after['balance'].min()
    only_spread = deepest >= 0  # в среднем денег хватает, риск только из-за разброса
    shortfall = -risky['low'].min() if only_spread else -deepest

    if events.empty:
        has_mandatory = False
    else:
        near = pd.to_datetime(events['date']).between(date - pd.Timedelta(days=REASON_DAYS), date)
        has_mandatory = (near & (events['amount'] < 0) & events['category'].isin(MANDATORY_CATEGORIES)).any()
    days_left = (date - forecast['date'].min()).days

    return [{
        'date': date,
        'shortfall': round(float(shortfall), 2),
        'probability': round(float(forecast['prob'].max()), 3),
        'reasons': _reasons(date, events, shortfall, only_spread, forecast['balance'].iloc[0] < 0),
        'advice': _advice(date, days_left, shortfall, has_mandatory, only_spread),
    }]
