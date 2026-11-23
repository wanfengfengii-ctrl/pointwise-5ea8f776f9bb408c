import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.finalize import close, initialize_accounts
from accounts.returns import DENIED, PENDING, RETURNED, request_return
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from ports import open_port
from records.export import PendingBatch, read, reprint
from rules.revisions import freeze
from rules.routes import RouteGraph
from work.tasks import advance


SAMPLE = Path(__file__).with_name('samples') / 'shift.json'
RETURN_BATCH = 'day-012'
RETURN_RECORD = 'sample-12'
RETURN_TARGET = (1,)
RETURN_RIGHT = (1, 3)
RETURN_LEFT = (0, 2)


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


def describe_return(publication):
    return {
        'status': 'published',
        'batch_id': publication.receipt.batch_id,
        'rows': [[row.record_id, list(row.path), list(row.edge_path), row.grams, row.category]
                 for row in publication.snapshot.rows],
        'totals': dict(publication.snapshot.category_totals),
        'returned_grams': publication.snapshot.returned_grams,
        'return_request_id': publication.snapshot.return_request_id,
        'original_grams': publication.receipt.original_grams,
        'detail_count': publication.receipt.detail_count,
    }


def _drain(work, accounts, batch_id, **controls):
    for _ in range(200):
        if advance(work, accounts, batch_id, **controls) is None:
            return
    raise RuntimeError(f'{batch_id} 退料演示未收敛')


def demo_waiting_return(folder):
    """时序一：右路回流暂停时办成退料，迟到回流放行后领 D 类 5 克＋退料 7 克。"""
    work_path = Path(folder) / 'waiting-work.sqlite'
    account_path = Path(folder) / 'waiting-accounts.sqlite'
    pause_right_return = lambda event: (
        event.kind == 'return' and event.key is not None
        and event.key.edge_path == RETURN_TARGET
    )
    with closing(open_port(work_path)) as work, closing(open_port(account_path)) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        load_sample(work)
        _drain(work, accounts, RETURN_BATCH, pause=pause_right_return)
        order_status = request_return(
            accounts, RETURN_BATCH, 'RR-WAITING', RETURN_RECORD, RETURN_TARGET,
        )
        for _ in range(20):
            event = advance(work, accounts, RETURN_BATCH, pause=pause_right_return)
            if event is None:
                break
        settled = request_return(
            accounts, RETURN_BATCH, 'RR-WAITING', RETURN_RECORD, RETURN_TARGET,
        )
        if order_status != PENDING or settled != RETURNED:
            raise SystemExit(f'退料接单状态错误：{order_status} -> {settled}')
        _drain(work, accounts, RETURN_BATCH)
        publication = close(accounts, RETURN_BATCH)
        expected = {
            'status': 'published', 'batch_id': RETURN_BATCH,
            'rows': [
                [RETURN_RECORD, ['S', 'L', 'D'], list(RETURN_LEFT), 5, 'D'],
                [RETURN_RECORD, ['S', 'R'], list(RETURN_TARGET), 7, None],
            ],
            'totals': {'D': 5}, 'returned_grams': 7,
            'return_request_id': 'RR-WAITING', 'original_grams': 12, 'detail_count': 2,
        }
        check_outcome('waiting-return', describe_return(publication), expected)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'return.waiting_return',
            'order': {'request_id': 'RR-WAITING', 'edge_path': list(RETURN_TARGET),
                      'register': order_status, 'settle': settled},
            'expected': expected, 'actual': describe_return(publication),
            'code_paths': ['accounts/returns.py', 'work/delivery.py', 'accounts/finalize.py'],
            'oracle': '右路 7 克先办成退料，截点 S/R 替代整棵右路；左路 5 克到 D，分类只汇总普通终点。',
        }, ensure_ascii=False, sort_keys=True))
        receipt_id = publication.receipt.receipt_id
        original_print = reprint(accounts, receipt_id=receipt_id)
    # 加工库关闭后重开封账库领取
    with closing(open_port(account_path)) as archive:
        initialize_accounts(archive)
        reopened = read(archive, batch_id=RETURN_BATCH)
        check_outcome('waiting-return-reopen', describe_return(reopened), expected)
        if reprint(archive, receipt_id=receipt_id) != original_print:
            raise SystemExit('waiting-return 重印与原固定账不符')
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'return.waiting_reopen',
            'receipt_id': receipt_id, 'claim_by': ['batch_id', 'receipt_id'],
            'print_matches': True,
        }, ensure_ascii=False, sort_keys=True))
        print(reprint(archive, receipt_id=receipt_id))


def demo_result_in_flight(folder):
    """时序二：右路终点已在加工端提交（结果在途）须拒退，待齐后签 D 类 12 克普通账。"""
    work_path = Path(folder) / 'flight-work.sqlite'
    account_path = Path(folder) / 'flight-accounts.sqlite'
    pause_right_result = lambda event: (
        event.kind == 'result' and event.key is not None
        and event.key.edge_path == RETURN_RIGHT
    )
    with closing(open_port(work_path)) as work, closing(open_port(account_path)) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        load_sample(work)
        _drain(work, accounts, RETURN_BATCH, pause=pause_right_result)
        request_return(accounts, RETURN_BATCH, 'RR-FLIGHT', RETURN_RECORD, RETURN_TARGET)
        for _ in range(10):
            event = advance(work, accounts, RETURN_BATCH, pause=pause_right_result)
            if event is None:
                break
        settled = request_return(
            accounts, RETURN_BATCH, 'RR-FLIGHT', RETURN_RECORD, RETURN_TARGET,
        )
        if settled != DENIED:
            raise SystemExit(f'在途终点应拒退，实得 {settled}')
        if not isinstance(close(accounts, RETURN_BATCH), PendingBatch):
            raise SystemExit('拒退待齐期间本批不应签发')
        _drain(work, accounts, RETURN_BATCH)
        publication = close(accounts, RETURN_BATCH)
        expected = {
            'status': 'published', 'batch_id': RETURN_BATCH,
            'rows': [
                [RETURN_RECORD, ['S', 'L', 'D'], list(RETURN_LEFT), 5, 'D'],
                [RETURN_RECORD, ['S', 'R', 'D'], list(RETURN_RIGHT), 7, 'D'],
            ],
            'totals': {'D': 12}, 'returned_grams': None,
            'return_request_id': None, 'original_grams': 12, 'detail_count': 2,
        }
        check_outcome('result-in-flight', describe_return(publication), expected)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'return.result_in_flight',
            'order': {'request_id': 'RR-FLIGHT', 'edge_path': list(RETURN_TARGET),
                      'settle': settled},
            'expected': expected, 'actual': describe_return(publication),
            'code_paths': ['accounts/returns.py', 'work/journal.py', 'accounts/finalize.py'],
            'oracle': '右路终点已提交即拒退，两路 5＋7 到 D，签普通 D 类 12 克，料账不含退料字段。',
        }, ensure_ascii=False, sort_keys=True))
        print(reprint(accounts, batch_id=RETURN_BATCH))


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

    # 在途支路退料两种时序及重开领账（独立临时库，互不影响原 v5 演示）
    with TemporaryDirectory(prefix='chengqiao-return-demo-') as return_folder:
        demo_waiting_return(return_folder)
        demo_result_in_flight(return_folder)


if __name__ == '__main__':
    main()
