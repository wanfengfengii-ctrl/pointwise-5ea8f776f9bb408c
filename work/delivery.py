import json
from dataclasses import dataclass

from accounts.acceptance import accept_batch_header, accept_completion, accept_result
from accounts.store import AccountStore
from intake.store import BatchHeader
from ports import transaction
from work.journal import CompletionFact, RelayJournal, WorkJournal
from work.lineage import OccurrenceKey, RouteOccurrence, SourceError, validate_lineage
from work.transform import TerminalResult


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    kind: str
    batch_id: str
    key: OccurrenceKey | None

    @classmethod
    def from_entry(cls, entry):
        key = OccurrenceKey.from_token(entry.batch_id, entry.token) if entry.token else None
        return cls(entry.kind, entry.batch_id, key)


class AcknowledgmentLost(RuntimeError):
    def __init__(self, event):
        self.event = event
        super().__init__(f'接收已提交，发送方未获确认：{event}')


def make_completion_fact(journal, header):
    keys = journal.completion_view(header.batch_id)
    if keys is None:
        return None
    return CompletionFact(header.batch_id, header.acceptance_id, header.revision_id, keys)


def receive_relay(accounts, occurrence):
    if not occurrence.key.edge_path:
        raise SourceError('回流端口只接收派生待办')
    with transaction(accounts):
        header = AccountStore(accounts).require_header(occurrence.key.batch_id)
        validate_lineage(occurrence, header)
        RelayJournal(accounts).receive(occurrence)


def _deliver_to_accounts(accounts, entry):
    payload = json.loads(entry.body)
    if entry.kind == 'header':
        accept_batch_header(accounts, BatchHeader.from_data(payload))
    elif entry.kind == 'child':
        receive_relay(accounts, RouteOccurrence.from_data(payload))
    elif entry.kind == 'result':
        accept_result(accounts, TerminalResult.from_data(payload))
    elif entry.kind == 'completion':
        accept_completion(accounts, CompletionFact.from_data(payload))
    else:
        raise SourceError(f'无效投递类型 {entry.kind}')


def deliver_pending(work, accounts, batch_id, *, pause=None, lose_ack=None):
    journal = WorkJournal(work)
    for entry in journal.pending_deliveries(batch_id):
        event = DeliveryEvent.from_entry(entry)
        if pause is not None and pause(event):
            if entry.kind == 'header':
                return None
            continue
        _deliver_to_accounts(accounts, entry)
        if lose_ack is not None and lose_ack(event):
            raise AcknowledgmentLost(event)
        with transaction(work):
            journal.record_confirmation(entry)
        return event
    return None


def return_relayed_work(work, accounts, batch_id, *, pause=None, lose_ack=None):
    relay = RelayJournal(accounts)
    for entry in relay.pending_returns(batch_id):
        event = DeliveryEvent.from_entry(entry)
        if pause is not None and pause(event):
            continue
        with transaction(work):
            WorkJournal(work).accept_returned_child(RouteOccurrence.from_data(json.loads(entry.body)))
        if lose_ack is not None and lose_ack(event):
            raise AcknowledgmentLost(event)
        with transaction(accounts):
            relay.record_confirmation(entry)
        return event
    return None
