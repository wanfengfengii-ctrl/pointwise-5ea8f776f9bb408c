from accounts.completion import assess_completion
from accounts.store import AccountStore
from accounts.totals import order_details, reconcile_weights, sum_by_category
from ports import initialize_port, transaction
from records.catalog import Publication, ReceiptCatalog
from records.details import make_detail_snapshot
from records.export import PendingBatch
from records.receipts import make_receipt
from rules.routes import require_name
from work.journal import RelayJournal


def initialize_accounts(connection):
    initialize_port(connection, 'accounts', (
        AccountStore(connection).initialize,
        ReceiptCatalog(connection).initialize,
        RelayJournal(connection).initialize,
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
