from accounts.store import AccountStore
from intake.records import validate_records
from intake.store import acceptance_number
from ports import transaction
from rules.routes import require_name
from rules.validation import validate_revision
from work.lineage import SourceError, resolve_path, subtree_contains
from work.transform import validate_terminal_result


def accept_batch_header(connection, header):
    try:
        require_name(header.batch_id, '批次号')
        require_name(header.revision_id, '冻结版本号')
        validate_revision(header.graph)
        validate_records(header.records, header.graph)
    except ValueError as error:
        raise SourceError('批头的冻结路线或原称无效') from error
    if header.acceptance_id != acceptance_number(header.batch_id):
        raise SourceError('批头的接纳号与批次不符')
    with transaction(connection):
        AccountStore(connection).register_batch(header)


def _reject_overlapping_return(connection, key):
    from accounts.returns import RETURNED, ReturnStore
    record = ReturnStore(connection).find_by_batch(key.batch_id)
    if record is not None and record.status == RETURNED:
        if key.record_id == record.record_id and subtree_contains(key.edge_path, record.edge_path):
            raise SourceError('普通终点与已确认退料截点重叠')


def accept_result(connection, result):
    store = AccountStore(connection)
    with transaction(connection):
        header = store.require_header(result.occurrence.key.batch_id)
        validate_terminal_result(result, header)
        _reject_overlapping_return(connection, result.occurrence.key)
        inserted = store.insert_result(result)
    return inserted


def accept_completion(connection, fact):
    store = AccountStore(connection)
    with transaction(connection):
        header = store.require_header(fact.batch_id)
        if fact.acceptance_id != header.acceptance_id or fact.revision_id != header.revision_id:
            raise SourceError('完成事实的接纳号或冻结版本不符')
        keys = set(fact.result_keys)
        if len(keys) != len(fact.result_keys):
            raise SourceError('完成事实重复列出同一次出现')
        for key in keys:
            _, path, _ = resolve_path(header, key)
            if header.graph.terminal_category(path[-1]) is None:
                raise SourceError('完成事实包含未到终点的路径')
        if {key.record_id for key in keys} != {record.record_id for record in header.records}:
            raise SourceError('完成事实遗漏原称记录')
        inserted = store.save_completion(fact)
    return inserted
