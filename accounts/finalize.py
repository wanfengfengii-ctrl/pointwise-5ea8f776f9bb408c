from accounts.completion import assess_completion
from accounts.returns import PENDING, RETURNED, ReturnStore
from accounts.store import AccountStore
from accounts.totals import (
    order_details,
    plan_return_book,
    reconcile_return_book,
    reconcile_weights,
    sum_by_category,
)
from ports import initialize_port, transaction
from records.catalog import Publication, ReceiptCatalog
from records.details import DetailRow, make_detail_snapshot
from records.export import PendingBatch
from records.receipts import make_receipt
from rules.routes import require_name
from work.journal import RelayJournal


def initialize_accounts(connection):
    initialize_port(connection, 'accounts', (
        AccountStore(connection).initialize,
        ReceiptCatalog(connection).initialize,
        RelayJournal(connection).initialize,
        ReturnStore(connection).initialize,
    ))


def close(connection, batch_id):
    require_name(batch_id, '批次号')
    store = AccountStore(connection)
    catalog = ReceiptCatalog(connection)
    with transaction(connection):
        existing = catalog.find_by_batch(batch_id)
        if existing is not None:
            return existing
        view = store.load_for_close(batch_id)
        return_record = None if view is None else view.return_record
        if return_record is not None and return_record.status == PENDING:
            return PendingBatch(batch_id)
        if return_record is not None and return_record.status == RETURNED:
            return _close_returned(connection, view, catalog, store, return_record)
        if not assess_completion(view).ready:
            return PendingBatch(batch_id)
        ordered = order_details(view.header.records, view.results)
        category_totals = sum_by_category(ordered)
        if not reconcile_weights(view.header.records, ordered, category_totals):
            return PendingBatch(batch_id)
        number = catalog.next_receipt_number()
        snapshot = make_detail_snapshot(ordered, category_totals)
        receipt = make_receipt(number, view.header, len(snapshot.rows))
        publication = Publication(receipt, snapshot)
        catalog.stage_publication(number, publication)
        store.mark_closed(batch_id, receipt.receipt_id)
    return publication


def _close_returned(connection, view, catalog, store, return_record):
    plan = plan_return_book(view.header, view.results, return_record)
    if plan is None or not assess_completion(view).ready:
        return PendingBatch(view.header.batch_id)
    ordinary_results, return_rows = plan
    category_totals = sum_by_category(ordinary_results)
    if not reconcile_return_book(
        view.header.records, ordinary_results, return_rows, category_totals,
    ):
        return PendingBatch(view.header.batch_id)
    expected_return = sum(row.grams for row in return_rows)
    if expected_return != return_record.grams:
        return PendingBatch(view.header.batch_id)
    ordinary_rows = tuple(DetailRow(
        result.occurrence.key.record_id, result.occurrence.path,
        result.occurrence.key.edge_path, result.occurrence.grams, result.category,
    ) for result in ordinary_results)
    position = {record.record_id: index for index, record in enumerate(view.header.records)}
    ordered = tuple(sorted(
        (*ordinary_rows, *return_rows),
        key=lambda row: (position[row.record_id], row.edge_path),
    ))
    number = catalog.next_receipt_number()
    snapshot = make_detail_snapshot(
        ordered, category_totals,
        returned_grams=expected_return, return_request_id=return_record.request_id,
    )
    receipt = make_receipt(number, view.header, len(snapshot.rows))
    publication = Publication(receipt, snapshot)
    catalog.stage_publication(number, publication)
    store.mark_closed(view.header.batch_id, receipt.receipt_id)
    return publication
