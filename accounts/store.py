import json
from dataclasses import dataclass

from intake.store import BatchHeader
from ports import encode
from work.journal import CompletionFact
from work.lineage import SourceError
from work.transform import TerminalResult


@dataclass(frozen=True, slots=True)
class AccountCloseView:
    header: BatchHeader
    results: tuple[TerminalResult, ...]
    completion: CompletionFact | None
    receipt_id: str | None


class AccountStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS account_batches (
                batch_id TEXT PRIMARY KEY NOT NULL,
                header TEXT NOT NULL,
                completion TEXT,
                receipt_id TEXT UNIQUE REFERENCES publications(receipt_id)
            )
        ''')
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS account_results (
                arrival INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES account_batches(batch_id),
                token TEXT NOT NULL,
                body TEXT NOT NULL,
                UNIQUE (batch_id, token)
            )
        ''')
        self.connection.execute('''
            CREATE INDEX IF NOT EXISTS results_by_batch ON account_results (batch_id, arrival)
        ''')

    def register_batch(self, header):
        body = encode(header.to_data())
        inserted = self.connection.execute('''
            INSERT INTO account_batches (batch_id, header) VALUES (?, ?)
            ON CONFLICT (batch_id) DO NOTHING
        ''', (header.batch_id, body)).rowcount
        if not inserted:
            row = self.connection.execute('''
                SELECT header FROM account_batches WHERE batch_id = ?
            ''', (header.batch_id,)).fetchone()
            if row[0] != body:
                raise SourceError('封账端收到同批号异载荷批头')

    def require_header(self, batch_id):
        row = self.connection.execute('''
            SELECT header FROM account_batches WHERE batch_id = ?
        ''', (batch_id,)).fetchone()
        if row is None:
            raise SourceError(f'封账端尚无批头 {batch_id}')
        return BatchHeader.from_data(json.loads(row[0]))

    def _batch_facts(self, batch_id):
        return self.connection.execute('''
            SELECT header, completion, receipt_id FROM account_batches WHERE batch_id = ?
        ''', (batch_id,)).fetchone()

    def _result_rows(self, batch_id):
        return self.connection.execute('''
            SELECT token, body FROM account_results WHERE batch_id = ? ORDER BY arrival
        ''', (batch_id,))

    def insert_result(self, result):
        key = result.occurrence.key
        body = encode(result.to_data())
        previous = self.connection.execute('''
            SELECT body FROM account_results WHERE batch_id = ? AND token = ?
        ''', (key.batch_id, key.token())).fetchone()
        if previous is not None:
            if previous[0] != body:
                raise SourceError('同一原出现收到异载荷')
            return False
        facts = self._batch_facts(key.batch_id)
        if facts is None:
            raise SourceError('结果没有对应批头')
        _, completion, receipt_id = facts
        if receipt_id is not None:
            raise SourceError('已签批次收到新增结果')
        if completion is not None and key.token() not in json.loads(completion)['result_tokens']:
            raise SourceError('结果超出已收到的批次完成范围')
        self.connection.execute('''
            INSERT INTO account_results (batch_id, token, body) VALUES (?, ?, ?)
        ''', (key.batch_id, key.token(), body))
        return True

    def save_completion(self, fact):
        facts = self._batch_facts(fact.batch_id)
        if facts is None:
            raise SourceError('完成事实没有对应批头')
        _, previous, receipt_id = facts
        body = encode(fact.to_data())
        if previous is not None:
            if previous != body:
                raise SourceError('同一批次收到不同完成事实')
            return False
        if receipt_id is not None:
            raise SourceError('已签批次收到新增完成事实')
        declared = {key.token() for key in fact.result_keys}
        if any(token not in declared for token, _ in self._result_rows(fact.batch_id)):
            raise SourceError('完成事实遗漏已入账结果')
        self.connection.execute('''
            UPDATE account_batches SET completion = ? WHERE batch_id = ?
        ''', (body, fact.batch_id))
        return True

    def load_for_close(self, batch_id):
        facts = self._batch_facts(batch_id)
        if facts is None:
            return None
        header, completion, receipt_id = facts
        return AccountCloseView(
            BatchHeader.from_data(json.loads(header)),
            tuple(TerminalResult.from_data(json.loads(body)) for _, body in self._result_rows(batch_id)),
            None if completion is None else CompletionFact.from_data(json.loads(completion)),
            receipt_id,
        )

    def mark_closed(self, batch_id, receipt_id):
        changed = self.connection.execute('''
            UPDATE account_batches SET receipt_id = ? WHERE batch_id = ? AND receipt_id IS NULL
        ''', (receipt_id, batch_id)).rowcount
        if changed != 1:
            raise SourceError('批次不存在或已封账')
