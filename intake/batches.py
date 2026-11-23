from dataclasses import dataclass

from intake.handoff import stage_root_work
from intake.records import validate_records
from intake.retries import compare_batch_request
from intake.store import BatchHeader, IntakeStore, acceptance_number
from ports import initialize_port, transaction
from rules.revisions import get_frozen_revision
from rules.routes import require_name
from rules.store import RuleStore
from work.journal import WorkJournal


@dataclass(frozen=True, slots=True)
class AcceptedBatch:
    batch_id: str
    acceptance_id: str
    revision_id: str


def initialize_work(connection):
    initialize_port(connection, 'work', (
        RuleStore(connection).initialize,
        IntakeStore(connection).initialize,
        WorkJournal(connection).initialize,
    ))


def accept(connection, batch_id, revision_id, records):
    require_name(batch_id, '批次号')
    store = IntakeStore(connection)
    with transaction(connection):
        revision = get_frozen_revision(connection, revision_id)
        ordered = validate_records(records, revision.graph)
        existing = store.find_batch(batch_id)
        if existing is None:
            acceptance_id = acceptance_number(batch_id)
            header = BatchHeader(batch_id, acceptance_id, revision_id, ordered, revision.graph)
            store.insert_batch(header)
            stage_root_work(WorkJournal(connection), header)
        else:
            acceptance_id = compare_batch_request(existing, revision_id, ordered)
    return AcceptedBatch(batch_id, acceptance_id, revision_id)
