"""Тесты основной логики: правила категоризации, регулярные платежи, прогноз остатка, разрывы.

Запуск из корня проекта:
    python -m pytest tests -v
"""
import os
import sys

import pandas as pd

# чтобы модули проекта импортировались, даже если запускать просто "pytest"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from categories import RETAIL, REFUND_IN, SALARY, TAXES  # noqa: E402
from cashflow_engine import build_forecast, find_gaps  # noqa: E402
from regular_payments import EVENT_COLUMNS, find_regular  # noqa: E402
from rules_categorizer import categorize_by_rules  # noqa: E402

IN = 'Поступление'
OUT = 'Списание'
START = pd.Timestamp('2026-10-01')


# ---------- categorize_by_rules ----------

def test_salary():
    purpose = ('Перечисление заработной платы (аванс) за октябрь 2024 г. '
               'по реестру № 38 от 25.10.2024. НДС не облагается')
    assert categorize_by_rules(purpose, OUT, 'ПАО «Банк»') == SALARY


def test_acquiring_with_zero_refund_is_retail():
    # в каждом реестре эквайринга есть "возврат покупки 0.00" - это выручка, а не возврат платежа
    purpose = ('Зачисление средств по операциям эквайринга. Мерчант №4814886. Дата реестра 28.01.2025. '
               'Сумма операций 2861-52, комиссия 51-47, возврат покупки 0.00. НДС не облагается')
    category = categorize_by_rules(purpose, IN, 'ПАО «Банк»')
    assert category == RETAIL
    assert category != REFUND_IN


def test_without_vat_is_not_taxes():
    # "Без налога (НДС)" - пометка про НДС, а не уплата налога
    purpose = 'Оплата по счету № 512 от 03.02.2026 за канцтовары и хозтовары. Без налога (НДС)'
    category = categorize_by_rules(purpose, OUT, 'ООО «Канцтовары-800»')
    assert category is not None
    assert category != TAXES


def test_empty_purpose_treasury_is_taxes():
    assert categorize_by_rules('', OUT, 'Казначейство России (ФНС России)') == TAXES


# ---------- find_regular ----------

def test_monthly_salary_is_found():
    # зарплата 10-го числа 4 месяца подряд одной суммой
    dates = pd.to_datetime(['2026-06-10', '2026-07-10', '2026-08-10', '2026-09-10'])
    ops = pd.DataFrame({
        'date': dates,
        'amount': -300_000.0,
        'direction': OUT,
        'inn': '0068275202',
        'counterparty': 'ПАО «Банк»',
        'purpose': 'Перечисление заработной платы',
        'category': SALARY,
    })
    events = find_regular(ops)  # прогноз с 11.09 на 30 дней

    assert list(events.columns) == EVENT_COLUMNS
    salary = events[events['category'] == SALARY]
    assert len(salary) == 1
    event = salary.iloc[0]
    assert event['name'] == 'Зарплата'
    assert event['amount'] == -300_000
    # 10.10.2026 - суббота: зарплату по ТК РФ платят накануне, в пятницу 09.10
    assert event['date'] == pd.Timestamp('2026-10-09')


# ---------- build_forecast и find_gaps ----------

def make_forecast(start_balance):
    """Поток 0, разброса нет, одно событие -150 000 на 3-й день."""
    flows = pd.DataFrame({'date': pd.date_range(START, periods=30, freq='D'), 'flow': 0.0, 'sigma': 0.0})
    events = pd.DataFrame([{'date': START + pd.Timedelta(days=2), 'amount': -150_000.0,
                            'name': 'Аренда', 'category': 'Аренда', 'source': 'manual'}])
    return build_forecast(start_balance, START - pd.Timedelta(days=1), flows, events), events


def test_forecast_balance_after_payment():
    forecast, _ = make_forecast(100_000)
    assert list(forecast.columns) == ['date', 'balance', 'low', 'high', 'prob']
    assert forecast['balance'].iloc[1] == 100_000   # до платежа ничего не меняется
    assert forecast['balance'].iloc[2] == -50_000   # 3-й день: 100 000 - 150 000
    assert forecast['balance'].iloc[-1] == -50_000  # дальше потока нет
    assert forecast['prob'].iloc[2] > 0.99          # без разброса минус - почти наверняка


def test_gap_found():
    forecast, events = make_forecast(100_000)
    gaps = find_gaps(forecast, events)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap['date'] == START + pd.Timedelta(days=2)
    assert gap['shortfall'] == 50_000
    assert gap['probability'] >= 0.4
    assert any('Аренда' in reason for reason in gap['reasons'])
    assert 2 <= len(gap['advice']) <= 3


def test_no_gap_with_big_balance():
    forecast, events = make_forecast(10_000_000)
    assert find_gaps(forecast, events) == []
