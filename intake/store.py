import json
from dataclasses import asdict, dataclass

from intake.records import WeighingRecord
from ports import encode
from rules.revisions import get_frozen_revision
from rules.routes import RouteGraph


class UnknownBatch(KeyError):
    pass


def acceptance_number(batch_id):
    return f'A:{batch_id}'


@dataclass(frozen=True, slots=True)
class StoredBatch:
    batch_id: str
    acceptance_id: str
    revision_id: str
    records: tuple[WeighingRecord, ...]


@dataclass(frozen=True, slots=True)
class BatchHeader(StoredBatch):
    graph: RouteGraph

    def to_data(self):
        return {
            'batch_id': self.batch_id,
            'acceptance_id': self.acceptance_id,
            'revision_id': self.revision_id,
            'records': [asdict(record) for record in self.records],
            'graph': self.graph.to_data(),
        }

    @classmethod
    def from_data(cls, data):
        return cls(
            data['batch_id'], data['acceptance_id'], data['revision_id'],
            tuple(WeighingRecord(**record) for record in data['records']),
            RouteGraph.from_data(data['graph']),
        )


class IntakeStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS intake_batches (
                batch_id TEXT PRIMARY KEY NOT NULL,
                acceptance_id TEXT NOT NULL UNIQUE,
                revision_id TEXT NOT NULL REFERENCES rule_revisions(revision_id),
                records TEXT NOT NULL
            )
        ''')

    def find_batch(self, batch_id):
        row = self.connection.execute('''
            SELECT acceptance_id, revision_id, records FROM intake_batches WHERE batch_id = ?
        ''', (batch_id,)).fetchone()
        if row is None:
            return None
        acceptance_id, revision_id, records = row
        return StoredBatch(
            batch_id, acceptance_id, revision_id,
            tuple(WeighingRecord(**record) for record in json.loads(records)),
        )

    def insert_batch(self, batch):
        self.connection.execute('''
            INSERT INTO intake_batches (batch_id, acceptance_id, revision_id, records)
            VALUES (?, ?, ?, ?)
        ''', (
            batch.batch_id, batch.acceptance_id, batch.revision_id,
            encode([asdict(record) for record in batch.records]),
        ))

    def load_batch_header(self, batch_id):
        batch = self.find_batch(batch_id)
        if batch is None:
            raise UnknownBatch(batch_id)
        revision = get_frozen_revision(self.connection, batch.revision_id)
        return BatchHeader(
            batch.batch_id, batch.acceptance_id, batch.revision_id, batch.records, revision.graph,
        )
