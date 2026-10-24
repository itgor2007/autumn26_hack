"""Бэктест: проверяем прогноз на прошлом.

Для каждой компании "отматываем время" на несколько дат назад, строим прогноз
на 30 дней так же, как в приложении, и сравниваем с тем, что было на самом деле.

Разрыв считаем так: остаток ушёл бы в минус, если бы владелец не доложил денег
и компания не взяла кредит (RESCUE_CATEGORIES). Именно об этом сервис
должен предупреждать заранее.
"""
import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, mean_absolute_error,
                             mean_squared_error, precision_score, recall_score, roc_auc_score)

from cashflow_engine import build_forecast
from categories import RESCUE_CATEGORIES
from data_prep import get_company, to_daily
from forecaster import forecast_flows
from regular_payments import find_regular

HORIZON = 30
MIN_HISTORY_DAYS = 90   # меньше трёх месяцев истории - не проверяем
STEP_DAYS = 28          # даты "отмотки" через каждые 4 недели
THRESHOLD = 0.4         # с какой вероятности считаем, что предупредили о разрыве


def cut_dates(daily):
    """Даты, на которые отматываем время для одной компании"""
    first = daily['date'].min() + pd.Timedelta(days=MIN_HISTORY_DAYS)
    last = daily['date'].max() - pd.Timedelta(days=HORIZON)
    if first > last:
        return []
    return list(pd.date_range(first, last, freq=f'{STEP_DAYS}D'))


def real_balance_without_rescue(company_ops, start_balance, dates):
    """Что было бы с остатком на самом деле, если убрать "спасательные" поступления"""
    future = company_ops[(company_ops['date'] >= dates[0]) & (company_ops['date'] <= dates[-1])]
    rescue = (future['amount'] > 0) & future['category'].isin(RESCUE_CATEGORIES)
    by_day = future[~rescue].groupby('date')['amount'].sum()
    return start_balance + pd.Series(dates).map(by_day).fillna(0).cumsum().values


def check_one(company_ops, daily_full, cut):
    """Один прогноз из прошлого. Возвращает строку для таблицы результатов или None."""
    history = company_ops[company_ops['date'] < cut]
    if history.empty:
        return None
    daily = daily_full[daily_full['date'] <= history['date'].max()]
    start_balance = daily['balance'].iloc[-1]

    events = find_regular(history, horizon=HORIZON)
    flows = forecast_flows(daily, history, events, horizon=HORIZON)
    forecast = build_forecast(start_balance, daily['date'].max(), flows, events)

    dates = pd.DatetimeIndex(forecast['date'])
    future_days = daily_full[(daily_full['date'] >= dates[0]) & (daily_full['date'] <= dates[-1])]
    # если в будущем есть пропуск в выписке или данных не хватает - сравнивать не с чем
    if len(future_days) < HORIZON or future_days['is_gap'].any():
        return None

    real = real_balance_without_rescue(company_ops, start_balance, dates)
    pred = forecast['balance'].values
    gap_days = np.where(real < 0)[0]

    return {
        'company_id': company_ops['company_id'].iloc[0],
        'cut_date': dates[0],
        'start_balance': start_balance,
        'pred_min': pred.min(),
        'real_min': real.min(),
        'prob': forecast['prob'].max(),
        'real_gap': start_balance >= 0 and len(gap_days) > 0,
        'days_to_gap': gap_days[0] + 1 if len(gap_days) > 0 else None,
        'mae': np.abs(pred - real).mean(),
        'mae_naive': np.abs(start_balance - real).mean(),
        'pred': pred,
        'real': real,
    }


def run_backtest(ops, progress=None):
    """ops - операции с категориями (можно сразу много компаний). Возвращает таблицу прогнозов."""
    rows = []
    companies = sorted(ops['company_id'].unique())
    for i, company_id in enumerate(companies):
        company_ops = get_company(ops, company_id)
        daily_full = to_daily(company_ops)
        for cut in cut_dates(daily_full):
            row = check_one(company_ops, daily_full, cut)
            if row is not None:
                rows.append(row)
        if progress is not None:
            progress((i + 1) / len(companies), company_id)
    return pd.DataFrame(rows)


def calc_metrics(results, threshold=THRESHOLD):
    """Метрики по таблице из run_backtest"""
    # окна, где уже в начале был минус, для разрывов не считаем - там предупреждать поздно
    clf = results[results['start_balance'] >= 0]
    y_true = clf['real_gap'].astype(bool)
    y_pred = clf['prob'] >= threshold

    real = np.concatenate(results['real'].values)
    pred = np.concatenate(results['pred'].values)
    naive = np.concatenate([np.full(len(r), b) for r, b in zip(results['real'], results['start_balance'])])

    caught = clf[y_true & y_pred]
    metrics = {
        'forecasts': len(results),
        'companies': results['company_id'].nunique(),
        'real_gaps': int(y_true.sum()),
        'accuracy': accuracy_score(y_true, y_pred),
        'precision': precision_score(y_true, y_pred, zero_division=0),
        'recall': recall_score(y_true, y_pred, zero_division=0),
        'f1': f1_score(y_true, y_pred, zero_division=0),
        'roc_auc': roc_auc_score(y_true, clf['prob']) if y_true.nunique() == 2 else None,
        'mae': mean_absolute_error(real, pred),
        'rmse': mean_squared_error(real, pred) ** 0.5,
        'mse': mean_squared_error(real, pred),
        'mae_naive': mean_absolute_error(real, naive),
        'rmse_naive': mean_squared_error(real, naive) ** 0.5,
        # за сколько дней до разрыва мы о нём предупредили (медиана по пойманным)
        'days_ahead': caught['days_to_gap'].median() if len(caught) else None,
    }
    matrix = confusion_matrix(y_true, y_pred, labels=[True, False])
    return metrics, matrix


def threshold_table(results, thresholds=(0.2, 0.3, 0.4, 0.5, 0.6, 0.7)):
    """Как меняются precision / recall / f1 при разном пороге вероятности"""
    rows = []
    for th in thresholds:
        m, _ = calc_metrics(results, th)
        rows.append({'порог': th, 'precision': m['precision'], 'recall': m['recall'],
                     'f1': m['f1'], 'accuracy': m['accuracy']})
    return pd.DataFrame(rows)


if __name__ == '__main__':
    # python evaluation.py data/all_operations.csv
    import sys
    import time

    from categorizer_ml import categorize
    from data_prep import load_statement

    path = sys.argv[1] if len(sys.argv) > 1 else 'data/all_operations.csv'
    started = time.time()
    ops, _ = load_statement(path)
    ops = categorize(ops)
    results = run_backtest(ops, progress=lambda share, c: print(f'\r{share:.0%} {c}', end=''))
    print(f'\nготово за {time.time() - started:.0f} с')

    metrics, matrix = calc_metrics(results)
    for name, value in metrics.items():
        print(f'{name:12} {value}')
    print('матрица ошибок (строки - было / не было, столбцы - предсказали / нет):')
    print(matrix)
    print(threshold_table(results).round(3).to_string(index=False))
