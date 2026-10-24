# Список категорий, который мы утвердили.
# Названия берём только отсюда, чтобы не было опечаток в разных файлах.

# поступления
RETAIL = 'Выручка: розница'
CLIENTS = 'Оплаты от клиентов'
MARKETPLACE = 'Маркетплейсы и агрегаторы'
LOANS_IN = 'Кредиты и займы полученные'
SUBSIDY = 'Субсидии, гранты, страховка'
REFUND_IN = 'Возвраты ошибочных платежей'

# списания
SALARY = 'Зарплата'
GPH = 'Самозанятые и ГПХ'
TAXES = 'Налоги'
RENT = 'Аренда'
UTILITIES = 'Коммуналка и связь'
LOANS_OUT = 'Кредиты, лизинг, займы'
SUPPLIERS = 'Поставщики: товары и материалы'
SERVICES = 'Услуги и подрядчики'
ADS = 'Реклама'
FUEL = 'Топливо и дороги'
BANK_FEES = 'Банковские комиссии'
OWNER = 'Выплаты владельцу'
BIG_PURCHASES = 'Крупные разовые покупки'
FINES = 'Штрафы и взыскания'

# в обе стороны
TRANSFERS = 'Внутренние переводы'
OTHER = 'Прочее'

INCOME_CATEGORIES = [RETAIL, CLIENTS, MARKETPLACE, LOANS_IN, SUBSIDY, REFUND_IN, TRANSFERS, OTHER]
EXPENSE_CATEGORIES = [SALARY, GPH, TAXES, RENT, UTILITIES, LOANS_OUT, SUPPLIERS, SERVICES,
                      ADS, FUEL, BANK_FEES, OWNER, BIG_PURCHASES, FINES, TRANSFERS, OTHER]

# на этих категориях учится модель прогноза (то, что идёт "потоком")
ML_CATEGORIES = [RETAIL, CLIENTS, MARKETPLACE, GPH, SUPPLIERS, SERVICES, ADS, FUEL, OTHER]

# обычно регулярные, их ищет regular_payments
REGULAR_CATEGORIES = [SALARY, TAXES, RENT, UTILITIES, LOANS_OUT, BANK_FEES]

# разовые и переводы - в прогноз не берём, только показываем в истории
EXCLUDED_CATEGORIES = [LOANS_IN, SUBSIDY, REFUND_IN, OWNER, BIG_PURCHASES, FINES, TRANSFERS]

# обязательные платежи, на них в первую очередь смотрим при поиске разрыва
MANDATORY_CATEGORIES = [SALARY, TAXES, RENT, UTILITIES, LOANS_OUT]
