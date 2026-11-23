import json
from dataclasses import dataclass

from ports import encode
from work.lineage import OccurrenceKey, RouteOccurrence, SourceError


@dataclass(frozen=True, slots=True)
class CompletionFact:
    batch_id: str
    acceptance_id: str
    revision_id: str
    result_keys: tuple[OccurrenceKey, ...]

    def to_data(self):
        return {
            'batch_id': self.batch_id,
            'acceptance_id': self.acceptance_id,
            'revision_id': self.revision_id,
            'result_tokens': [key.token() for key in sorted(self.result_keys)],
        }

    @classmethod
    def from_data(cls, data):
        batch_id = data['batch_id']
        return cls(
            batch_id, data['acceptance_id'], data['revision_id'],
            tuple(OccurrenceKey.from_token(batch_id, token) for token in data['result_tokens']),
        )


@dataclass(frozen=True, slots=True)
class JournalDelivery:
    batch_id: str
    kind: str
    token: str
    body: str


class WorkJournal:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS work_occurrences (
                sequence INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batches(batch_id),
                token TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'ready', 'handoff', 'expanded', 'terminal', 'returned'
                )),
                body TEXT NOT NULL,
                UNIQUE (batch_id, token)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS work_deliveries (
                sequence INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES intake_batches(batch_id),
                kind TEXT NOT NULL CHECK (kind IN (
                    'header', 'child', 'result', 'completion',
                    'return_request', 'return_outcome'
                )),
                token TEXT NOT NULL,
                body TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1)),
                UNIQUE (batch_id, kind, token)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS work_returns (
                request_id TEXT PRIMARY KEY NOT NULL,
                batch_id TEXT NOT NULL,
                record_id TEXT NOT NULL,
                edge_path TEXT NOT NULL,
                verdict TEXT NOT NULL CHECK (verdict IN ('returned', 'denied')),
                UNIQUE (batch_id, request_id)
            )
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS ready_occurrences ON work_occurrences (batch_id, state, sequence)
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS unconfirmed_deliveries
            ON work_deliveries (batch_id, confirmed, sequence)
        ''')

    def _add_occurrence(self, occurrence, state):
        self.connection.execute('''
            INSERT INTO work_occurrences (batch_id, token, state, body) VALUES (?, ?, ?, ?)
        ''', (
            occurrence.key.batch_id, occurrence.key.token(), state, encode(occurrence.to_data()),
        ))

    def enqueue_root(self, occurrence):
        if occurrence.key.edge_path:
            raise SourceError('根待办不能含冻结边')
        self._add_occurrence(occurrence, 'ready')

    def next_pending(self, batch_id):
        row = self.connection.execute('''
            SELECT body FROM work_occurrences
            WHERE batch_id = ? AND state = 'ready' ORDER BY sequence LIMIT 1
        ''', (batch_id,)).fetchone()
        return None if row is None else RouteOccurrence.from_data(json.loads(row[0]))

    def _finish_ready(self, occurrence, state):
        changed = self.connection.execute('''
            UPDATE work_occurrences SET state = ?
            WHERE batch_id = ? AND token = ? AND body = ? AND state = 'ready'
        ''', (
            state, occurrence.key.batch_id, occurrence.key.token(), encode(occurrence.to_data()),
        )).rowcount
        if changed != 1:
            raise SourceError('待办不存在、载荷不符或尚不可执行')

    def save_expansion(self, parent, expansion):
        self._finish_ready(parent, 'expanded')
        returned_cuts = self.returned_cuts(parent.key.batch_id)
        for child in expansion.children:
            if any(
                record_id == child.key.record_id and self._within_cut(child.key.edge_path, cut)
                for record_id, cut in returned_cuts
            ):
                self._add_occurrence(child, 'returned')
                continue
            self._add_occurrence(child, 'handoff')
            self.stage_delivery(child.key.batch_id, 'child', child.key.token(), child.to_data())

    @staticmethod
    def _within_cut(edge_path, cut):
        return edge_path[:len(cut)] == cut

    def returned_cuts(self, batch_id):
        rows = self.connection.execute('''
            SELECT record_id, edge_path FROM work_returns
            WHERE batch_id = ? AND verdict = 'returned'
        ''', (batch_id,))
        return set((record_id, tuple(json.loads(edge_path))) for record_id, edge_path in rows)

    def find_return_decision(self, batch_id, request_id):
        row = self.connection.execute('''
            SELECT verdict FROM work_returns WHERE batch_id = ? AND request_id = ?
        ''', (batch_id, request_id)).fetchone()
        return None if row is None else row[0]

    def save_return_decision(self, request):
        self.connection.execute('''
            INSERT INTO work_returns (request_id, batch_id, record_id, edge_path, verdict)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            request.request_id, request.batch_id, request.record_id,
            encode(list(request.edge_path)), request.verdict,
        ))

    def mark_subtree_returned(self, batch_id, record_id, cut):
        rows = self.connection.execute('''
            SELECT token, state, body FROM work_occurrences WHERE batch_id = ?
        ''', (batch_id,)).fetchall()
        for token, state, body in rows:
            key = OccurrenceKey.from_token(batch_id, token)
            if key.record_id != record_id or not self._within_cut(key.edge_path, cut):
                continue
            if state == 'terminal':
                raise SourceError('退料范围与已提交终点冲突')
            if state in ('ready', 'handoff'):
                self.connection.execute('''
                    UPDATE work_occurrences SET state = 'returned'
                    WHERE batch_id = ? AND token = ? AND state IN ('ready', 'handoff')
                ''', (batch_id, token))

    def stage_return_outcome(self, request):
        payload = {
            'request_id': request.request_id,
            'batch_id': request.batch_id,
            'record_id': request.record_id,
            'edge_path': list(request.edge_path),
            'verdict': request.verdict,
        }
        self.stage_delivery(request.batch_id, 'return_outcome', request.token(), payload)

    def save_terminal_result(self, result):
        occurrence = result.occurrence
        self._finish_ready(occurrence, 'terminal')
        self.stage_delivery(occurrence.key.batch_id, 'result', occurrence.key.token(), result.to_data())

    def accept_returned_child(self, child):
        if not child.key.edge_path:
            raise SourceError('根待办没有回流交接')
        address = (child.key.batch_id, child.key.token())
        row = self.connection.execute('''
            SELECT state, body FROM work_occurrences WHERE batch_id = ? AND token = ?
        ''', address).fetchone()
        if row is None or row[1] != encode(child.to_data()):
            raise SourceError('回流与已登记的派生待办不符')
        if row[0] == 'handoff':
            self.connection.execute('''
                UPDATE work_occurrences SET state = 'ready' WHERE batch_id = ? AND token = ?
            ''', address)
        # 'returned' 待办保持退料资格失效：迟到回流不能恢复执行。

    def stage_delivery(self, batch_id, kind, token, payload):
        self.connection.execute('''
            INSERT INTO work_deliveries (batch_id, kind, token, body) VALUES (?, ?, ?, ?)
        ''', (batch_id, kind, token, encode(payload)))

    def stage_completion_if_absent(self, batch_id, fact):
        exists = self.connection.execute('''
            SELECT 1 FROM work_deliveries
            WHERE batch_id = ? AND kind = 'completion' AND token = ''
        ''', (batch_id,)).fetchone()
        if exists is None:
            self.stage_delivery(batch_id, 'completion', '', fact.to_data())

    def pending_deliveries(self, batch_id):
        rows = self.connection.execute('''
            SELECT batch_id, kind, token, body FROM work_deliveries
            WHERE batch_id = ? AND confirmed = 0 ORDER BY sequence
        ''', (batch_id,))
        return tuple(JournalDelivery(*row) for row in rows)

    def record_confirmation(self, entry):
        changed = self.connection.execute('''
            UPDATE work_deliveries SET confirmed = 1
            WHERE batch_id = ? AND kind = ? AND token = ? AND body = ?
        ''', (entry.batch_id, entry.kind, entry.token, entry.body)).rowcount
        if changed != 1:
            raise SourceError('确认与原投递不符')

    def completion_view(self, batch_id):
        rows = self.connection.execute('''
            SELECT token, state FROM work_occurrences WHERE batch_id = ? ORDER BY sequence
        ''', (batch_id,))
        terminal_keys = []
        for token, state in rows:
            if state in ('ready', 'handoff'):
                return None
            if state == 'terminal':
                terminal_keys.append(OccurrenceKey.from_token(batch_id, token))
        return tuple(sorted(terminal_keys)) if terminal_keys else None

class RelayJournal:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS relay_occurrences (
                sequence INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES account_batches(batch_id),
                token TEXT NOT NULL,
                body TEXT NOT NULL,
                returned INTEGER NOT NULL DEFAULT 0 CHECK (returned IN (0, 1)),
                UNIQUE (batch_id, token)
            )
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS pending_returns ON relay_occurrences (batch_id, returned, sequence)
        ''')

    def receive(self, occurrence):
        address = (occurrence.key.batch_id, occurrence.key.token())
        body = encode(occurrence.to_data())
        inserted = self.connection.execute('''
            INSERT INTO relay_occurrences (batch_id, token, body) VALUES (?, ?, ?)
            ON CONFLICT (batch_id, token) DO NOTHING
        ''', (*address, body)).rowcount
        if not inserted:
            row = self.connection.execute('''
                SELECT body FROM relay_occurrences WHERE batch_id = ? AND token = ?
            ''', address).fetchone()
            if row[0] != body:
                raise SourceError('中继收到同标识异载荷')

    def pending_returns(self, batch_id):
        rows = self.connection.execute('''
            SELECT batch_id, token, body FROM relay_occurrences
            WHERE batch_id = ? AND returned = 0 ORDER BY sequence
        ''', (batch_id,))
        return tuple(JournalDelivery(batch, 'return', token, body) for batch, token, body in rows)

    def record_confirmation(self, entry):
        changed = self.connection.execute('''
            UPDATE relay_occurrences SET returned = 1 WHERE batch_id = ? AND token = ? AND body = ?
        ''', (entry.batch_id, entry.token, entry.body)).rowcount
        if changed != 1:
            raise SourceError('回流确认与原中继记录不符')
