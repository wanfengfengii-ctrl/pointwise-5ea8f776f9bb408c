import json
from dataclasses import dataclass

from records.details import DetailSnapshot, decode_snapshot, encode_snapshot
from records.receipts import PublishedReceipt, render_receipt


@dataclass(frozen=True, slots=True)
class Publication:
    receipt: PublishedReceipt
    snapshot: DetailSnapshot


class ReceiptCatalog:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS publications (
                number INTEGER PRIMARY KEY,
                batch_id TEXT NOT NULL UNIQUE REFERENCES account_batches(batch_id),
                receipt_id TEXT NOT NULL UNIQUE,
                receipt TEXT NOT NULL,
                details TEXT NOT NULL
            )
        ''')

    def next_receipt_number(self):
        number, = self.connection.execute('SELECT COALESCE(MAX(number), 0) + 1 FROM publications').fetchone()
        return number

    def stage_publication(self, number, publication):
        receipt = publication.receipt
        self.connection.execute('''
            INSERT INTO publications (number, batch_id, receipt_id, receipt, details)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            number, receipt.batch_id, receipt.receipt_id, render_receipt(receipt),
            encode_snapshot(publication.snapshot),
        ))

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        receipt, details = row
        return Publication(PublishedReceipt(**json.loads(receipt)), decode_snapshot(details))

    def find_by_batch(self, batch_id):
        return self._decode(self.connection.execute('''
            SELECT receipt, details FROM publications WHERE batch_id = ?
        ''', (batch_id,)).fetchone())

    def find_by_receipt(self, receipt_id):
        return self._decode(self.connection.execute('''
            SELECT receipt, details FROM publications WHERE receipt_id = ?
        ''', (receipt_id,)).fetchone())
