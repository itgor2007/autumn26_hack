import pandas as pd


def forecast_flows(daily, ops, events, horizon=30):
    # прогноз нерегулярных потоков на horizon дней: date, flow, sigma
    # TODO: baseline по дням недели, потом модель
    start = daily['date'].max() + pd.Timedelta(days=1)
    dates = pd.date_range(start, periods=horizon, freq='D')
    return pd.DataFrame({'date': dates, 'flow': 0.0, 'sigma': 0.0})
