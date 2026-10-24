import pandas as pd

from categories import REGULAR_CATEGORIES, RESCUE_CATEGORIES

WEEKS = 26          # берём последние полгода (на бэктесте 26 недель лучше, чем 4, 8 и 12)
MIN_DAYS = 14       # если хороших дней меньше - берём всю историю, какая есть


def flow_operations(ops):
    """Операции, которые прогнозирует forecaster.

    Регулярные платежи сюда не входят - их считает regular_payments.
    "Спасательные" поступления (владелец докладывает деньги, кредит) тоже не входят.
    """
    regular = ops['category'].isin(REGULAR_CATEGORIES)
    rescue = (ops['amount'] > 0) & ops['category'].isin(RESCUE_CATEGORIES)
    return ops[~regular & ~rescue]


def daily_flow(daily, ops):
    """Сумма таких операций по дням (без дней с пропусками в выписке)"""
    flow = flow_operations(ops).groupby('date')['amount'].sum()
    table = daily[['date', 'is_gap']].copy()
    table['flow'] = table['date'].map(flow).fillna(0)
    # дни, где в выписке пропали данные, не берём - там нули не настоящие
    return table[~table['is_gap']]


def forecast_flows(daily, ops, events, horizon=30):
    """Baseline: средний поток по дню недели за последние полгода.

    Возвращает date, flow, sigma. sigma - насколько обычно реальный день
    отличается от прогноза, нужна для вероятности разрыва.
    """
    start = daily['date'].max() + pd.Timedelta(days=1)
    dates = pd.date_range(start, periods=horizon, freq='D')

    table = daily_flow(daily, ops)
    history = table[table['date'] >= start - pd.Timedelta(weeks=WEEKS)]
    if len(history) < MIN_DAYS:
        history = table

    if history.empty:
        return pd.DataFrame({'date': dates, 'flow': 0.0, 'sigma': 0.0})

    weekday = history['date'].dt.day_of_week
    by_weekday = history.groupby(weekday)['flow'].mean()

    error = history['flow'] - weekday.map(by_weekday)
    sigma = error.std() if len(error) > 1 else 0.0

    result = pd.DataFrame({'date': dates})
    result['flow'] = result['date'].dt.day_of_week.map(by_weekday).fillna(0)
    result['sigma'] = sigma
    return result
