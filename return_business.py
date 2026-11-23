"""在途支路退料业务核验：用 return_cases 的接单时刻演示两种时序与重开领账。"""
import json
from contextlib import closing

from accounts.finalize import close, initialize_accounts
from accounts.returns import DENIED, PENDING, RETURNED, request_return
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from ports import open_port
from records.export import PendingBatch, read, reprint
from return_cases import BATCH, LEFT, RECORD, RIGHT, TARGET, drain, prepare_case
from rules.routes import RouteEdge, RouteGraph
from work.tasks import advance


def _book(publication):
    return {
        'rows': [[list(row.edge_path), row.grams, row.category] for row in publication.snapshot.rows],
        'category_totals': dict(publication.snapshot.category_totals),
        'returned_grams': publication.snapshot.returned_grams,
        'return_request_id': publication.snapshot.return_request_id,
        'original_grams': publication.receipt.original_grams,
        'detail_count': publication.receipt.detail_count,
    }


def run_waiting_return():
    """右路回流暂停时办成退料，放行迟到回流后仍只签 D 类 5 克、退料 7 克。"""
    with closing(open_port(':memory:')) as work, closing(open_port(':memory:')) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        prepare_case(work, accounts, 'waiting-return')
        status = request_return(accounts, BATCH, 'RR-WAIT', RECORD, TARGET)
        assert status == PENDING, status
        # 暂停右路回流期间结清退料
        for _ in range(20):
            event = advance(
                work, accounts, BATCH,
                pause=lambda e: e.kind == 'return' and e.key is not None
                and e.key.edge_path == TARGET,
            )
            if event is None:
                break
        settled = request_return(accounts, BATCH, 'RR-WAIT', RECORD, TARGET)
        assert settled == RETURNED, settled
        # 退料已结清，截点替代右路子树；放行迟到回流不改变料账
        drain(work, accounts)
        publication = close(accounts, BATCH)
        book = _book(publication)
        assert book['rows'] == [[list(LEFT), 5, 'D'], [list(TARGET), 7, None]], book
        assert book['category_totals'] == {'D': 5}, book
        assert book['returned_grams'] == 7 and book['return_request_id'] == 'RR-WAIT', book
        assert book['original_grams'] == 12 and book['detail_count'] == 2, book
        print(json.dumps({'scenario': 'waiting-return', 'status': settled, 'book': book},
                         ensure_ascii=False))
        return publication, accounts


def run_result_in_flight():
    """右路终点已在加工端提交（结果在途）须拒退，待齐后签 D 类 12 克普通账。"""
    with closing(open_port(':memory:')) as work, closing(open_port(':memory:')) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        prepare_case(work, accounts, 'result-in-flight')
        assert request_return(accounts, BATCH, 'RR-FLIGHT', RECORD, TARGET) == PENDING
        for _ in range(10):
            event = advance(
                work, accounts, BATCH,
                pause=lambda e: e.kind == 'result' and e.key is not None
                and e.key.edge_path == RIGHT,
            )
            if event is None:
                break
        assert request_return(accounts, BATCH, 'RR-FLIGHT', RECORD, TARGET) == DENIED
        assert close(accounts, BATCH) == PendingBatch(BATCH)
        drain(work, accounts)
        publication = close(accounts, BATCH)
        book = _book(publication)
        assert book['rows'] == [[list(LEFT), 5, 'D'], [list(RIGHT), 7, 'D']], book
        assert book['category_totals'] == {'D': 12}, book
        assert book['returned_grams'] is None and book['return_request_id'] is None, book
        print(json.dumps({'scenario': 'result-in-flight', 'status': DENIED, 'book': book},
                         ensure_ascii=False))


def run_before_path():
    """封账端刚收批头即退料：路径尚未生成也可选，签 D 类 5 克、退料 7 克。"""
    with closing(open_port(':memory:')) as work, closing(open_port(':memory:')) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        prepare_case(work, accounts, 'before-path')
        assert request_return(accounts, BATCH, 'RR-BEFORE', RECORD, TARGET) == PENDING
        drain(work, accounts)
        publication = close(accounts, BATCH)
        book = _book(publication)
        assert book['rows'] == [[list(LEFT), 5, 'D'], [list(TARGET), 7, None]], book
        assert book['category_totals'] == {'D': 5} and book['returned_grams'] == 7, book
        print(json.dumps({'scenario': 'before-path', 'book': book}, ensure_ascii=False))


def run_zero_gram_boundary():
    """三条 S→D 平行边（5、7、0），退掉边 (1,) 后零克边缺项待齐，齐后三条行全保留。"""
    graph = RouteGraph(
        ('S', 'D'),
        (RouteEdge('S', 'D', 5), RouteEdge('S', 'D', 7), RouteEdge('S', 'D', 0)),
        (('D', 'D'),),
    )
    with closing(open_port(':memory:')) as work, closing(open_port(':memory:')) as accounts:
        initialize_work(work)
        initialize_accounts(accounts)
        from rules.revisions import freeze
        freeze(work, 'parallel-1', graph)
        accept(work, 'P', 'parallel-1', (WeighingRecord('r', 'S', 12),))
        advance(work, accounts, 'P')
        request_return(accounts, 'P', 'RR-ZERO', 'r', (1,))
        drain(work, accounts, 'P', pause=lambda e: e.kind == 'result'
              and e.key is not None and e.key.edge_path == (2,))
        assert close(accounts, 'P') == PendingBatch('P')
        drain(work, accounts, 'P')
        publication = close(accounts, 'P')
        book = _book(publication)
        assert book['rows'] == [[[0], 5, 'D'], [[1], 7, None], [[2], 0, 'D']], book
        assert book['category_totals'] == {'D': 5} and book['returned_grams'] == 7, book
        print(json.dumps({'scenario': 'zero-gram-parallel', 'book': book}, ensure_ascii=False))


def run_reopen_claim():
    """加工库关闭后，封账库重开仍按原批次号/成果号领取固定退料账并重印。"""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix='chengqiao-return-') as folder:
        work_path = Path(folder) / 'work.sqlite'
        account_path = Path(folder) / 'accounts.sqlite'
        with closing(open_port(work_path)) as work, closing(open_port(account_path)) as accounts:
            initialize_work(work)
            initialize_accounts(accounts)
            prepare_case(work, accounts, 'waiting-return')
            request_return(accounts, BATCH, 'RR-REOPEN', RECORD, TARGET)
            for _ in range(20):
                event = advance(
                    work, accounts, BATCH,
                    pause=lambda e: e.kind == 'return' and e.key is not None
                    and e.key.edge_path == TARGET,
                )
                if event is None:
                    break
            drain(work, accounts)
            publication = close(accounts, BATCH)
            receipt_id = publication.receipt.receipt_id
            original_print = reprint(accounts, receipt_id=receipt_id)
        # 加工库离线后，仅重开封账库领取
        with closing(open_port(account_path)) as archive:
            initialize_accounts(archive)
            by_receipt = read(archive, receipt_id=receipt_id)
            by_batch = read(archive, batch_id=BATCH)
            assert by_receipt == publication == by_batch
            assert reprint(archive, batch_id=BATCH) == original_print
            assert _book(by_receipt)['returned_grams'] == 7
        print(json.dumps({
            'scenario': 'reopen-claim', 'receipt_id': receipt_id,
            'claim_by': ['receipt_id', 'batch_id'], 'print_matches': True,
        }, ensure_ascii=False))


def main():
    run_before_path()
    run_waiting_return()
    run_result_in_flight()
    run_zero_gram_boundary()
    run_reopen_claim()
    print('RETURN_CASES_ALL_PASS')


if __name__ == '__main__':
    main()
