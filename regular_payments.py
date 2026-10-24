import pandas as pd

# события - это будущие платежи, про которые мы знаем заранее
# source: 'regular' (нашли в истории), 'tax' (налоговый календарь), 'manual' (ввёл пользователь)
EVENT_COLUMNS = ['date', 'amount', 'name', 'category', 'source']


def find_regular(ops, horizon=30):
    # TODO: найти регулярные платежи и вернуть их даты и суммы на horizon дней вперёд
    return pd.DataFrame(columns=EVENT_COLUMNS)
