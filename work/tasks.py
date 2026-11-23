from intake.store import IntakeStore, UnknownBatch
from ports import transaction
from rules.routes import require_name
from work.delivery import (
    deliver_pending,
    make_completion_fact,
    process_return_request,
    return_relayed_work,
)
from work.journal import WorkJournal
from work.transform import Expansion, process_occurrence


def execute_occurrence(work, batch_id):
    journal = WorkJournal(work)
    with transaction(work):
        header = IntakeStore(work).load_batch_header(batch_id)
        occurrence = journal.next_pending(batch_id)
        if occurrence is None:
            return None
        processed = process_occurrence(occurrence, header)
        if isinstance(processed, Expansion):
            journal.save_expansion(occurrence, processed)
        else:
            journal.save_terminal_result(processed)
        fact = make_completion_fact(journal, header)
        if fact is not None:
            journal.stage_completion_if_absent(batch_id, fact)
    return occurrence.key


def advance(work, accounts, batch_id, *, pause=None, lose_ack=None):
    require_name(batch_id, '批次号')
    if IntakeStore(work).find_batch(batch_id) is None:
        raise UnknownBatch(batch_id)
    # 退料申请优先于普通回流交接：暂停目标回流时仍可办退料。
    for transfer in (process_return_request, deliver_pending, return_relayed_work):
        event = transfer(work, accounts, batch_id, pause=pause, lose_ack=lose_ack)
        if event is not None:
            return event
    return execute_occurrence(work, batch_id)
