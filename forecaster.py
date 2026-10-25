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


def weekday_profile(daily, ops):
    """Средний поток по каждому дню недели и sigma.

    Возвращает (by_weekday, sigma, history): by_weekday - среднее для пн..вс (0..6),
    history - дни, по которым считали. Отдельно, чтобы показать это в логе расчёта.
    """
    start = daily['date'].max() + pd.Timedelta(days=1)
    table = daily_flow(daily, ops)
    history = table[table['date'] >= start - pd.Timedelta(weeks=WEEKS)]
    if len(history) < MIN_DAYS:
        history = table

    if history.empty:
        return pd.Series(dtype=float), 0.0, history

    weekday = history['date'].dt.day_of_week
    by_weekday = history.groupby(weekday)['flow'].mean()

    # sigma - насколько обычно реальный день отличается от среднего своего дня недели
    error = history['flow'] - weekday.map(by_weekday)
    sigma = error.std() if len(error) > 1 else 0.0
    return by_weekday, sigma, history


def forecast_flows(daily, ops, events, horizon=30):
    """Baseline: средний поток по дню недели за последние полгода.

    Возвращает date, flow, sigma. sigma нужна для вероятности разрыва.
    """
    start = daily['date'].max() + pd.Timedelta(days=1)
    dates = pd.date_range(start, periods=horizon, freq='D')
    by_weekday, sigma, history = weekday_profile(daily, ops)

    result = pd.DataFrame({'date': dates})
    result['flow'] = result['date'].dt.day_of_week.map(by_weekday).fillna(0)
    result['sigma'] = sigma
    return result
