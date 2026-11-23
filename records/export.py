from dataclasses import dataclass

from records.catalog import ReceiptCatalog
from records.details import encode_snapshot
from records.receipts import render_receipt
from rules.routes import require_name


@dataclass(frozen=True, slots=True)
class PendingBatch:
    batch_id: str


class UnknownReceipt(LookupError):
    pass


def read(connection, *, batch_id=None, receipt_id=None):
    if (batch_id is None) == (receipt_id is None):
        raise ValueError('请提供一个批次号或成果号')
    catalog = ReceiptCatalog(connection)
    if batch_id is not None:
        require_name(batch_id, '批次号')
        publication = catalog.find_by_batch(batch_id)
        return PendingBatch(batch_id) if publication is None else publication
    require_name(receipt_id, '成果号')
    publication = catalog.find_by_receipt(receipt_id)
    if publication is None:
        raise UnknownReceipt(receipt_id)
    return publication


def reprint(connection, *, batch_id=None, receipt_id=None):
    publication = read(connection, batch_id=batch_id, receipt_id=receipt_id)
    if isinstance(publication, PendingBatch):
        return publication
    return render_receipt(publication.receipt) + '\n' + encode_snapshot(publication.snapshot)
