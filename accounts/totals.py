from rules.weights import require_grams
from work.lineage import resolve_path, subtree_contains


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


def reconcile_return_book(records, ordinary_results, return_rows, category_totals):
    """普通终点与退料截点共同覆盖原称；分类只核对普通终点。"""
    source_balance = {record.record_id: record.grams for record in records}
    category_balance = dict(category_totals)
    for result in ordinary_results:
        occurrence = result.occurrence
        if occurrence.key.record_id not in source_balance:
            return False
        if result.category not in category_balance:
            return False
        source_balance[occurrence.key.record_id] -= require_grams(occurrence.grams)
        category_balance[result.category] -= require_grams(occurrence.grams)
    for row in return_rows:
        if row.record_id not in source_balance:
            return False
        source_balance[row.record_id] -= require_grams(row.grams)
    return (
        all(balance == 0 for balance in source_balance.values())
        and all(balance == 0 for balance in category_balance.values())
    )


def plan_return_book(header, results, return_record):
    """按冻结路线核定普通终点与退料截点的完整覆盖，返回 (普通行, 退料行) 或 None。"""
    from records.details import DetailRow

    position = {record.record_id: index for index, record in enumerate(header.records)}
    cuts = ((return_record.record_id, return_record.edge_path),)
    result_index = {
        (result.occurrence.key.record_id, result.occurrence.key.edge_path): result
        for result in results
    }
    ordinary_rows = []
    return_rows = []
    for record in header.records:
        expected_terminals = _terminal_paths(header, record)
        for edge_path in expected_terminals:
            covered_by_cut = next(
                (cut for (cut_record, cut) in cuts
                 if cut_record == record.record_id and subtree_contains(edge_path, cut)),
                None,
            )
            result = result_index.get((record.record_id, edge_path))
            if covered_by_cut is not None:
                if result is not None:
                    return None
                continue
            if result is None:
                return None
            ordinary_rows.append(result)
        for cut_record, cut in cuts:
            if cut_record != record.record_id:
                continue
            _, node_path, grams = resolve_path(
                header, _key(header.batch_id, cut_record, cut),
            )
            return_rows.append(DetailRow(cut_record, node_path, cut, grams, None))
    if set(result_index) - {
        (result.occurrence.key.record_id, result.occurrence.key.edge_path)
        for result in ordinary_rows
    }:
        return None
    ordinary_rows = tuple(sorted(ordinary_rows, key=lambda result: (
        position[result.occurrence.key.record_id], result.occurrence.key.edge_path,
    )))
    return_rows = tuple(sorted(return_rows, key=lambda row: (position[row.record_id], row.edge_path)))
    return ordinary_rows, return_rows


def _key(batch_id, record_id, edge_path):
    from work.lineage import OccurrenceKey
    return OccurrenceKey(batch_id, record_id, edge_path)


def _terminal_paths(header, record):
    """枚举该原称在冻结路线上的全部终点边序路径（含零克边）。"""
    terminals = []

    def walk(node, edge_path):
        outgoing = header.graph.outgoing_edges(node)
        if not outgoing:
            terminals.append(tuple(edge_path))
            return
        for index, edge in outgoing:
            walk(edge.target, [*edge_path, index])

    walk(record.start, [])
    return terminals
