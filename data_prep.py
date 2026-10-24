import pandas as pd

NEEDED_COLUMNS = ['company_id', 'operation_date', 'amount', 'direction',
                  'counterparty_inn', 'counterparty_name', 'payment_purpose', 'balance_eod']

INCOME = 'Поступление'
EXPENSE = 'Списание'


def read_csv_file(file):
    # file - это либо путь, либо файл из st.file_uploader
    params = dict(sep=';', dtype={'company_id': str, 'operation_id': str, 'counterparty_inn': str})
    try:
        return pd.read_csv(file, encoding='utf-8-sig', **params)
    except UnicodeDecodeError:
        # если файл пересохранили в excel, он может быть в windows-1251
        if hasattr(file, 'seek'):
            file.seek(0)
        return pd.read_csv(file, encoding='cp1251', **params)


def load_statement(file):
    """Читает выписку и чистит её. Возвращает (операции, список проблем в данных)."""
    df = read_csv_file(file)
    problems = []

    missing = [col for col in NEEDED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError('В файле не хватает колонок: ' + ', '.join(missing))

    if 'operation_id' not in df.columns:
        df['operation_id'] = df['company_id'] + '-' + (df.index + 1).astype(str)

    # суммы иногда бывают с запятой или пробелами
    for col in ['amount', 'balance_eod']:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.replace(' ', '').str.replace(',', '.')
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df['operation_date'] = pd.to_datetime(df['operation_date'], errors='coerce')
    df['direction'] = df['direction'].str.strip()

    bad_rows = (df['operation_date'].isna() | df['amount'].isna() | df['balance_eod'].isna()
                | ~df['direction'].isin([INCOME, EXPENSE]))
    if bad_rows.sum() > 0:
        problems.append(f'Пропущено строк с ошибками (дата, сумма или направление): {bad_rows.sum()}')
        df = df[~bad_rows]

    for col in ['counterparty_inn', 'counterparty_name', 'payment_purpose']:
        df[col] = df[col].fillna('').astype(str).str.strip()

    # одинаковые строки с разным номером операции - это дубли выгрузки
    # (проверили: если их убрать, остаток на конец дня сходится)
    dup_cols = ['company_id', 'operation_date', 'amount', 'direction',
                'counterparty_inn', 'counterparty_name', 'payment_purpose']
    dups = df.duplicated(dup_cols)
    if dups.sum() > 0:
        problems.append(f'Удалено дублей: {dups.sum()}')
        df = df[~dups]

    empty_purpose = (df['payment_purpose'] == '').sum()
    if empty_purpose > 0:
        problems.append(f'Операций без назначения платежа: {empty_purpose}')
    empty_inn = (df['counterparty_inn'] == '').sum()
    if empty_inn > 0:
        problems.append(f'Операций без ИНН контрагента: {empty_inn}')

    ops = pd.DataFrame({
        'company_id': df['company_id'],
        'operation_id': df['operation_id'],
        'date': df['operation_date'],
        # приход с плюсом, расход с минусом
        'amount': df['amount'].where(df['direction'] == INCOME, -df['amount']),
        'direction': df['direction'],
        'inn': df['counterparty_inn'],
        'counterparty': df['counterparty_name'],
        'purpose': df['payment_purpose'],
        'balance_eod': df['balance_eod'],
    })
    ops = ops.sort_values(['company_id', 'date', 'operation_id']).reset_index(drop=True)
    return ops, problems


def get_company(ops, company_id):
    return ops[ops['company_id'] == company_id].reset_index(drop=True)


def to_daily(ops):
    """Операции одной компании -> таблица по дням: date, inflow, outflow, balance, is_gap"""
    if ops['company_id'].nunique() > 1:
        raise ValueError('to_daily работает с одной компанией, сначала вызови get_company')

    inflow = ops[ops['amount'] > 0].groupby('date')['amount'].sum()
    outflow = -ops[ops['amount'] < 0].groupby('date')['amount'].sum()
    balance = ops.groupby('date')['balance_eod'].last()

    days = pd.date_range(ops['date'].min(), ops['date'].max(), freq='D')
    daily = pd.DataFrame(index=days)
    daily['inflow'] = inflow
    daily['outflow'] = outflow
    daily['balance'] = balance
    daily[['inflow', 'outflow']] = daily[['inflow', 'outflow']].fillna(0)
    daily['has_ops'] = daily['balance'].notna()
    daily['is_gap'] = False

    # Проверяем остаток: вчерашний остаток + приход - расход должен давать сегодняшний.
    # Если не сходится - значит, в выписке пропали дни, и эти дни нельзя
    # считать "днями без операций", их надо выкинуть из обучения.
    op_days = daily[daily['has_ops']]
    expected = op_days['balance'].shift(1) + op_days['inflow'] - op_days['outflow']
    broken = (expected - op_days['balance']).abs() > 1
    prev_day = op_days.index.to_series().shift(1)
    for day in op_days.index[broken]:
        daily.loc[prev_day[day] + pd.Timedelta(days=1):day, 'is_gap'] = True

    # в выходные операций нет, остаток тот же, что в последний рабочий день
    daily['balance'] = daily['balance'].ffill()

    daily.index.name = 'date'
    return daily.reset_index()


def data_summary(ops, daily):
    """Короткая сводка по истории компании, чтобы показать в интерфейсе"""
    gaps = daily['is_gap'] & ~daily['is_gap'].shift(1, fill_value=False)
    return {
        'first_date': daily['date'].min(),
        'last_date': daily['date'].max(),
        'days': len(daily),
        'operations': len(ops),
        'gaps': int(gaps.sum()),
        'gap_days': int(daily['is_gap'].sum()),
        'last_balance': daily['balance'].iloc[-1],
        'short_history': len(daily) < 90,
    }
