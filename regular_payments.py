"""Регулярные платежи: находим их в истории и продлеваем на horizon дней вперёд.

Как работает find_regular:
1. Берём списания из регулярных категорий (REGULAR_CATEGORIES: зарплата, налоги, аренда,
   коммуналка, кредиты, комиссии) и группируем по контрагенту (ИНН) + категории.
2. Для каждой группы смотрим последние полгода и определяем, как часто платят:
   - 2 раза в месяц в одни и те же дни  -> по дням месяца (зарплата 10-го и 25-го)
   - раз в месяц                        -> по дню месяца (аренда 5-го, связь 10-го)
   - раз в месяц, но день "плавает"     -> по среднему дню, в названии "(дата примерная)" (ТКО)
   - интервал ~7 или ровно 14 дней      -> шагаем от последнего платежа
   Платежи с разницей до 2 дней склеиваем в один (зарплата частями 5, 6, 7-го).
3. Проверяем стабильность: суммы похожие, платёж не пропал давно. Иначе - не регулярный.
   Исключение: 2 одинаковых платежа ровно через месяц - новый договор (лизинг), берём.
4. Продлеваем на horizon дней. Выходной или праздник -> переносим на следующий рабочий день.
   Исключение - зарплата: по ТК РФ (ст. 136) её платят накануне выходного, так и в данных.
   Зарплату считаем по компании целиком (сумма за день), а не по каждому сотруднику:
   у отдельных сотрудников суммы скачут, а в сумме - стабильно.
5. ЕНП 28-го: если налоги в истории были, а детектор их не нашёл (суммы скачут) -
   добавляем налог 28-го числа по налоговому календарю. Если нашёл - не дублируем.

Суммы событий со знаком, как в ops: списание с минусом.
"""
import pandas as pd

from categories import *

# события - это будущие платежи, про которые мы знаем заранее
# source: 'regular' (нашли в истории), 'tax' (налоговый календарь), 'manual' (ввёл пользователь)
EVENT_COLUMNS = ['date', 'amount', 'name', 'category', 'source']

HISTORY_DAYS = 180     # смотрим на последние полгода: старые привычки могли измениться
MIN_PAYMENTS = 3       # меньше трёх платежей - не можем говорить о регулярности
MAX_INTERVAL_CV = 0.35  # насколько могут "гулять" интервалы (разброс / среднее)
MAX_AMOUNT_CV = 0.5    # насколько могут "гулять" суммы
TAX_DAY = 28           # срок уплаты ЕНП

# Нерабочие праздничные дни РФ (без переносов, которые меняются каждый год)
HOLIDAYS = {(1, d) for d in range(1, 9)} | {(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}


def is_workday(day):
    return day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS


def next_workday(day):
    """Если день выходной или праздник - переносим на ближайший следующий рабочий."""
    while not is_workday(day):
        day += pd.Timedelta(days=1)
    return day


def previous_workday(day):
    """Если день выходной или праздник - переносим на ближайший предыдущий рабочий."""
    while not is_workday(day):
        day -= pd.Timedelta(days=1)
    return day


def shift_to_workday(day, category):
    if category == SALARY:
        return previous_workday(day)
    return next_workday(day)


def _cv(values):
    """Коэффициент вариации: разброс относительно среднего. 0 - все одинаковые."""
    values = pd.Series(values, dtype=float)
    if len(values) < 2 or values.mean() == 0:
        return 0.0
    return values.std() / abs(values.mean())


def _day_distance(a, b):
    """Расстояние между днями месяца "по кругу": 30-е и 1-е - соседние дни."""
    d = abs(a - b)
    return min(d, 30 - d)


def _anchor_days(days, count):
    """Типичные дни месяца, в которые идёт платёж (1 или 2 дня).

    Берём самый частый день. Для двух платежей в месяц второй день - самый частый
    среди дней, которые хотя бы на 4 дня дальше от первого (бывает 15-го и 20-го).
    """
    counts = pd.Series(days).value_counts()
    first = counts.index[0]
    anchors = [first]
    if count == 2:
        far = [d for d in counts.index if _day_distance(d, first) >= 4]
        if not far:
            return None
        anchors.append(far[0])
    return sorted(anchors)


def _month_dates(anchor, start, end, category=None):
    """Даты с днём месяца anchor между start и end (с переносом с выходных)."""
    result = []
    month = pd.Timestamp(start.year, start.month, 1) - pd.DateOffset(months=1)
    while month <= end:
        day = min(anchor, month.days_in_month)  # 31-е в коротком месяце -> последний день
        date = shift_to_workday(pd.Timestamp(month.year, month.month, day), category)
        if start <= date <= end:
            result.append(date)
        month += pd.DateOffset(months=1)
    return result


def _fits_month_days(days, count, tolerance=3):
    """Почти все платежи (80%+) идут в одни и те же дни месяца (+-3 дня на выходные)?"""
    anchors = _anchor_days(days, count)
    if anchors is None:
        return False
    close = [min(_day_distance(d, a) for a in anchors) <= tolerance for d in days]
    return sum(close) >= 0.8 * len(close)


def _new_monthly_contract(payments):
    """Всего 2 платежа, но одинаковые и ровно через месяц - похоже на новый договор
    (лизинг, аренда), который только начался."""
    if len(payments) != 2:
        return False
    gap = (payments.index[1] - payments.index[0]).days
    return 27 <= gap <= 35 and abs(payments.iloc[0] - payments.iloc[1]) < 0.01


def _payments_per_month(payments):
    """Сколько раз в месяц обычно платят (медиана по месяцам, месяцы без платежей тоже считаем)."""
    months = payments.index.to_period('M')
    all_months = pd.period_range(months.min(), months.max(), freq='M')
    counts = pd.Series(months).value_counts().reindex(all_months, fill_value=0)
    return int(round(counts.median()))


def _merge_close(payments, gap_days=2):
    """Платежи с разницей до 2 дней считаем одним (зарплату иногда платят частями 5, 6, 7-го).

    Дата объединённого платежа - первый день, сумма - сумма всех частей.
    """
    dates = payments.index.to_series()
    batch = (dates.diff().dt.days > gap_days).cumsum()
    merged = payments.groupby(batch.values).sum()
    merged.index = dates.groupby(batch.values).min().values
    return merged


def _nearest_anchor(day, anchors):
    return min(anchors, key=lambda a: _day_distance(day, a))


def _median_day(anchor, days):
    """Уточняем день: медиана отклонений от anchor (по кругу), чтобы "плавающий" день
    (5, 8, 6, 11...) давал середину, а не случайный самый частый день."""
    offsets = [(d - anchor + 15) % 30 - 15 for d in days]
    day = int(round(anchor + pd.Series(offsets).median()))
    if day < 1:
        day += 30
    if day > 31:
        day -= 30
    return day


def _paid_every_month(payments, min_months=4):
    """Платёж есть в каждом месяце подряд (по одному), хоть и в разные дни - как вывоз ТКО."""
    months = payments.index.to_period('M')
    span = (months.max() - months.min()).n + 1
    return span >= min_months and months.nunique() == span and len(payments) <= span + 1


def _detect_group(group, start, end):
    """Одна группа платежей (контрагент + категория) -> список будущих событий или []."""
    payments = _merge_close(group.groupby('date')['amount'].sum().sort_index())
    if len(payments) < MIN_PAYMENTS and not _new_monthly_contract(payments):
        return []

    # платёж давно не появлялся - похоже, его больше нет (договор закончился)
    intervals = payments.index.to_series().diff().dt.days.dropna()
    step = intervals.median()
    if (start - payments.index[-1]).days > max(45, step * 1.5):
        return []

    category = group['category'].iloc[0]
    if category == SALARY:
        name = 'Зарплата'
    else:
        name = f"{category}: {group['counterparty'].iloc[0]}"
    events = []

    def add(date, amount):
        events.append({'date': date, 'amount': amount, 'name': name,
                       'category': category, 'source': 'regular'})

    # Определяем, как часто платят
    days = payments.index.day
    per_month = _payments_per_month(payments)
    even_intervals = _cv(intervals) <= MAX_INTERVAL_CV
    if per_month == 2 and _fits_month_days(days, 2):
        kind = 'month'          # 2 раза в месяц в одни и те же дни (зарплата 10-го и 25-го)
    elif per_month == 1 and (_fits_month_days(days, 1) or _paid_every_month(payments)
                             or (even_intervals and 25 <= step <= 35)):
        kind = 'month'          # раз в месяц (день может "плавать", как у ТКО)
    elif 5 <= step <= 9 and even_intervals:
        kind = 'step'           # раз в неделю
    elif 12 <= step <= 16 and _cv(intervals) <= 0.15:
        kind = 'step'           # ровно раз в 2 недели
    else:
        return []               # интервалы неровные - платёж не регулярный

    if kind == 'step':
        # раз в неделю или раз в 2 недели: шагаем от последнего платежа
        if _cv(payments.tail(6)) > MAX_AMOUNT_CV:
            return []
        period = 7 if step <= 9 else 14
        amount = payments.tail(3).median()
        date = payments.index[-1] + pd.Timedelta(days=period)
        while date <= end:
            if date >= start:
                add(shift_to_workday(date, category), amount)
            date += pd.Timedelta(days=period)
        return events

    # kind == 'month': раз или два раза в месяц - ищем типичные дни месяца
    anchors = _anchor_days(days, per_month)
    if anchors is None:
        return []
    floating = not _fits_month_days(days, per_month)
    if floating:
        name += ' (дата примерная)'
    # каждый платёж относим к ближайшему "своему" дню месяца
    nearest = [_nearest_anchor(d, anchors) for d in days]
    by_anchor = pd.Series(payments.values, index=nearest)
    for anchor in anchors:
        amounts = by_anchor[by_anchor.index == anchor]
        if len(amounts) < 2 or _cv(amounts.tail(6)) > MAX_AMOUNT_CV:
            return []
        amount = amounts.tail(3).median()
        # стабильный день - берём самый частый; "плавающий" - середину
        day = _median_day(anchor, [d for d, a in zip(days, nearest) if a == anchor]) if floating else anchor
        for date in _month_dates(day, start, end, category):
            add(date, amount)
    return events


def _tax_events(history, found, start, end):
    """ЕНП 28-го числа, если налоги платили, а детектор их не нашёл."""
    taxes = history[history['category'] == TAXES]
    if taxes.empty or (start - taxes['date'].max()).days > 95:
        return []

    events = []
    monthly = taxes.groupby(taxes['date'].dt.to_period('M'))['amount'].sum()
    for date in _month_dates(TAX_DAY, start, end, TAXES):
        # не дублировать: детектор уже поставил налог рядом с этой датой
        found_dates = pd.to_datetime(found['date'])  # у пустой таблицы тип колонки не дата
        if any((found['category'] == TAXES) & ((found_dates - date).abs() <= pd.Timedelta(days=5))):
            continue
        # сумма: как в этом же месяце год назад (квартальные налоги), иначе медиана за полгода
        same_month_last_year = date.to_period('M') - 12
        if same_month_last_year in monthly.index:
            amount = monthly[same_month_last_year]
        else:
            amount = monthly.tail(6).median()
        events.append({'date': date, 'amount': amount, 'name': 'ЕНП (срок уплаты 28-го)',
                       'category': TAXES, 'source': 'tax'})
    return events


def find_regular(ops, horizon=30):
    """Операции одной компании (с колонкой category) -> будущие события на horizon дней.

    Колонки: date, amount (со знаком, списание < 0), name, category, source.
    """
    if ops.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    start = ops['date'].max() + pd.Timedelta(days=1)
    end = start + pd.Timedelta(days=horizon - 1)

    history = ops[(ops['amount'] < 0) & ops['category'].isin(REGULAR_CATEGORIES)]
    recent = history[history['date'] > start - pd.Timedelta(days=HISTORY_DAYS)]

    # контрагент: ИНН, а если его нет - название (например "Сотрудник 02")
    key = recent['inn'].where(recent['inn'] != '', recent['counterparty'])
    key = key.where(recent['category'] != SALARY, 'вся зарплата')
    events = []
    for _, group in recent.groupby([key, recent['category']]):
        events += _detect_group(group, start, end)

    found = pd.DataFrame(events, columns=EVENT_COLUMNS)
    events += _tax_events(history, found, start, end)

    result = pd.DataFrame(events, columns=EVENT_COLUMNS)
    result['date'] = pd.to_datetime(result['date'])
    return result.sort_values('date').reset_index(drop=True)


def add_manual_event(events, date, amount, name, category=OTHER):
    """Платёж, который ввёл пользователь (например, "клиент заплатит 15-го").

    amount со знаком: поступление +, списание -.
    """
    new = pd.DataFrame([{'date': pd.Timestamp(date), 'amount': amount, 'name': name,
                         'category': category, 'source': 'manual'}])
    if events.empty:
        return new[EVENT_COLUMNS]
    return pd.concat([events, new], ignore_index=True).sort_values('date').reset_index(drop=True)


if __name__ == '__main__':
    # Проверка на одной компании: python regular_payments.py C001
    import sys

    from categorizer_ml import categorize
    from data_prep import get_company, load_statement

    company_id = sys.argv[1] if len(sys.argv) > 1 else 'C001'
    ops, _ = load_statement('data/all_operations.csv')
    ops = categorize(get_company(ops, company_id))
    events = find_regular(ops)
    pd.set_option('display.width', 200)
    print(f'{company_id}: история до {ops["date"].max():%d.%m.%Y}, событий на 30 дней: {len(events)}')
    print(events.to_string())
