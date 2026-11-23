from rules.weights import require_grams


def order_details(records, results):
    position = {record.record_id: index for index, record in enumerate(records)}
    return tuple(sorted(results, key=lambda result: (
        position[result.occurrence.key.record_id], result.occurrence.key.edge_path,
    )))


def sum_by_category(results):
    totals = {}
    for result in results:
        grams = require_grams(result.occurrence.grams)
        totals[result.category] = totals.get(result.category, 0) + grams
    return totals


def reconcile_weights(records, results, category_totals):
    source_balance = {record.record_id: record.grams for record in records}
    category_balance = dict(category_totals)
    sources_seen = set()
    categories_seen = set()
    for result in results:
        occurrence = result.occurrence
        record_id = occurrence.key.record_id
        category = result.category
        if record_id not in source_balance or category not in category_balance:
            return False
        grams = require_grams(occurrence.grams)
        sources_seen.add(record_id)
        categories_seen.add(category)
        source_balance[record_id] -= grams
        category_balance[category] -= grams
    return (
        sources_seen == source_balance.keys()
        and categories_seen == category_balance.keys()
        and all(balance == 0 for balance in source_balance.values())
        and all(balance == 0 for balance in category_balance.values())
    )
