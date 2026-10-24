# autumn26_hack
Хакатон осень 2026. Сервис прогноза денежных потоков и кассовых разрывов для малого бизнеса.

## Запуск

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Датасет из чата распаковать в папку `data/` (должны получиться `data/all_operations.csv`, `data/companies.csv`, `data/statements/`). В git он не попадает.

## Кто что делает

| Файл | Что делает | Кто |
|---|---|---|
| `categories.py` | список категорий | общий |
| `data_prep.py` | загрузка и чистка выписки, таблица по дням | Саша |
| `categorizer_ml.py` | категоризация (правила + ML) | Саша |
| `forecaster.py` | прогноз потоков, бэктест | Саша |
| `app.py` | интерфейс | Саша |
| `rules_categorizer.py` | правила категоризации | напарница |
| `regular_payments.py` | регулярные платежи, налоги, ручные события | напарница |
| `cashflow_engine.py` | прогноз остатка, разрывы, советы | напарница |

## Что передаём между модулями

- `load_statement(file)` -> `(ops, problems)`. В `ops` колонки: `company_id, operation_id, date, amount, direction, inn, counterparty, purpose, balance_eod`. `amount` со знаком: приход +, расход -.
- `to_daily(ops)` -> `date, inflow, outflow, balance, has_ops, is_gap`. `is_gap = True` там, где в выписке пропали данные.
- `categorize_by_rules(purpose, direction, counterparty)` -> категория из `categories.py` или `None`
- `categorize(ops)` -> `ops` + `category, category_source`
- `find_regular(ops)` -> события: `date, amount, name, category, source`
- `forecast_flows(daily, ops, events)` -> `date, flow, sigma` (только нерегулярные потоки)
- `build_forecast(start_balance, start_date, flows, events)` -> `date, balance, low, high`
- `find_gaps(forecast, events)` -> список словарей `date, shortfall, probability, reasons, advice`
