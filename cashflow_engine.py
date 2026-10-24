import pandas as pd


def build_forecast(start_balance, start_date, flows, events):
    # flows: date, flow, sigma (из forecaster)
    # events: date, amount, name, category, source (из regular_payments + ручные)
    # должна вернуть: date, balance, low, high
    # TODO: пока просто ровная линия
    forecast = pd.DataFrame({'date': flows['date']})
    forecast['balance'] = start_balance
    forecast['low'] = start_balance
    forecast['high'] = start_balance
    return forecast


def find_gaps(forecast, events):
    # должна вернуть список словарей: date, shortfall, probability, reasons, advice
    # TODO
    return []
