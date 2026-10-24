from categories import OTHER
from rules_categorizer import categorize_by_rules


def categorize(ops):
    # пока только правила, ML добавлю потом
    ops = ops.copy()
    ops['category'] = [categorize_by_rules(p, d, c)
                       for p, d, c in zip(ops['purpose'], ops['direction'], ops['counterparty'])]
    ops['category_source'] = 'rules'
    no_category = ops['category'].isna()
    ops.loc[no_category, 'category'] = OTHER
    ops.loc[no_category, 'category_source'] = 'none'
    return ops
