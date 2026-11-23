import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.finalize import close, initialize_accounts
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from ports import open_port
from records.export import PendingBatch, read, reprint
from rules.revisions import freeze
from rules.routes import RouteGraph
from work.tasks import advance


SAMPLE = Path(__file__).with_name('samples') / 'shift.json'


def load_sample(work, path=SAMPLE):
    data = json.loads(path.read_text(encoding='utf-8'))
    revision_id = data['revision_id']
    freeze(work, revision_id, RouteGraph.from_data(data['graph']))
    return tuple(accept(
        work, batch['batch_id'], revision_id,
        [WeighingRecord(**record) for record in batch['records']],
    ) for batch in data['batches'])


def drain(work, accounts, batch_id, **controls):
    while advance(work, accounts, batch_id, **controls) is not None:
        pass


def describe(result):
    if isinstance(result, PendingBatch):
        return {'status': 'pending', 'batch_id': result.batch_id}
    return {
        'status': 'published',
        'batch_id': result.receipt.batch_id,
        'rows': [[row.record_id, list(row.path), row.grams, row.category] for row in result.snapshot.rows],
        'totals': dict(result.snapshot.category_totals),
    }


def check_outcome(phase, actual, expected):
    if actual != expected:
        raise SystemExit('BATCH_CLOSURE_MISMATCH ' + json.dumps({
            'phase': phase, 'expected': expected, 'actual': actual,
        }, sort_keys=True))


def main():
    expected_pending = {'status': 'pending', 'batch_id': 'day-012'}
    expected_independent = {
        'status': 'published', 'batch_id': 'day-ready',
        'rows': [['sample-3', ['D'], 3, 'D']], 'totals': {'D': 3},
    }
    expected_target = {
        'status': 'published', 'batch_id': 'day-012',
        'rows': [['sample-12', ['S', 'L', 'D'], 5, 'D'], ['sample-12', ['S', 'R', 'D'], 7, 'D']],
        'totals': {'D': 12},
    }

    def pause_right(event):
        return event.kind == 'child' and event.key.edge_path == (1,)

    with TemporaryDirectory(prefix='chengqiao-demo-') as folder:
        work_path = Path(folder) / 'work.sqlite'
        archive_path = Path(folder) / 'accounts.sqlite'
        with closing(open_port(work_path)) as work, closing(open_port(archive_path)) as accounts:
            initialize_work(work)
            initialize_accounts(accounts)
            accepted = load_sample(work)
            drain(work, accounts, 'day-012', pause=pause_right)
            drain(work, accounts, 'day-ready')
            pending = describe(close(accounts, 'day-012'))
            independent = describe(close(accounts, 'day-ready'))
            check_outcome('right-handoff-paused', pending, expected_pending)
            check_outcome('independent-batch', independent, expected_independent)
            drain(work, accounts, 'day-012')
            publication = close(accounts, 'day-012')
            actual_target = describe(publication)
            check_outcome('handoff-restored', actual_target, expected_target)
            check_outcome('intake-replay', load_sample(work), accepted)
            receipt_id = publication.receipt.receipt_id
            original_print = reprint(accounts, receipt_id=receipt_id)

        with closing(open_port(archive_path)) as archive:
            initialize_accounts(archive)
            reopened = read(archive, receipt_id=receipt_id)
            check_outcome('archive-reopen', describe(reopened), expected_target)
            if reopened != publication or reprint(archive, batch_id='day-012') != original_print:
                raise SystemExit('BATCH_CLOSURE_MISMATCH ' + json.dumps({
                    'phase': 'fixed-reprint', 'actual': describe(reopened),
                    'receipt_id': reopened.receipt.receipt_id,
                }, sort_keys=True))

        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v5',
            'input': json.loads(SAMPLE.read_text(encoding='utf-8')),
            'expected': {'paused': expected_pending, 'independent': expected_independent},
            'actual': {'paused': pending, 'independent': independent},
            'downstream_expected': expected_target,
            'downstream_actual': describe(reopened),
            'code_paths': ['work/journal.py', 'work/delivery.py', 'accounts/finalize.py', 'records/export.py'],
            'oracle': '样例边给定左路 5 克和右路 7 克，人工相加为 D 类 12 克。右路尚未交接时原批无资格，独立样件在 D 直接归类。',
        }, ensure_ascii=False, sort_keys=True))
        print(original_print)


if __name__ == '__main__':
    main()
