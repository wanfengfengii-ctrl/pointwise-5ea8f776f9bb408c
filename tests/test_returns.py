import json

from accounts.finalize import close, initialize_accounts
from accounts.returns import DENIED, PENDING, RETURNED, request_return
from accounts.store import AccountStore
from intake.batches import accept
from intake.records import WeighingRecord
from records.export import PendingBatch, read, reprint
from rules.routes import RouteEdge, RouteGraph
from tests.fixtures import LEFT, RIGHT, ProjectTestCase, at_event
from work.delivery import AcknowledgmentLost
from work.lineage import SourceError
from work.tasks import advance


TARGET = (1,)


def parallel_zero_graph():
    return RouteGraph(
        ('S', 'D'),
        (RouteEdge('S', 'D', 5), RouteEdge('S', 'D', 7), RouteEdge('S', 'D', 0)),
        (('D', 'D'),),
    )


class ReturnRequestTests(ProjectTestCase):
    def deliver_header(self, batch_id='A'):
        event = advance(self.work, self.accounts, batch_id)
        self.assertEqual(event.kind, 'header')

    def test_request_validation_leaves_no_record(self):
        self.accept_route()
        self.deliver_header()
        # 非法路径：空、非元组、布尔序位、越界序位、负序位
        for edge_path in ((), [1], (True,), (99,), (-1,)):
            with self.subTest(edge_path=edge_path):
                with self.assertRaises(ValueError):
                    request_return(self.accounts, 'A', 'REQ-BAD', 'r', edge_path)
        for kwargs in (
            dict(batch_id='', request_id='q', record_id='r', edge_path=TARGET),
            dict(batch_id='A', request_id=' ', record_id='r', edge_path=TARGET),
            dict(batch_id='A', request_id='q', record_id='r', edge_path=()),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    request_return(self.accounts, **kwargs)
        # 未知批头、未知原称
        with self.assertRaises(SourceError):
            request_return(self.accounts, 'UNKNOWN', 'q', 'r', TARGET)
        with self.assertRaises(ValueError):
            request_return(self.accounts, 'A', 'q', 'missing-record', TARGET)
        self.assertIsNone(AccountStore(self.accounts).load_for_close('A').return_record)

    def test_unknown_batch_header_rejected(self):
        self.accept_route()
        with self.assertRaises(SourceError):
            request_return(self.accounts, 'A', 'q', 'r', TARGET)

    def test_same_id_different_params_and_second_order_rejected(self):
        self.accept_route()
        self.deliver_header()
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        # 同单号异参
        with self.assertRaises(ValueError):
            request_return(self.accounts, 'A', 'REQ-1', 'r', (0,))
        # 同批第二单号
        with self.assertRaises(ValueError):
            request_return(self.accounts, 'A', 'REQ-2', 'r', TARGET)
        # 同号同参查询
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        # 原单号跨批绑定冲突
        self.accept_route('B', records=(WeighingRecord('r', 'S', 12),))
        self.deliver_header('B')
        with self.assertRaises(ValueError):
            request_return(self.accounts, 'B', 'REQ-1', 'r', TARGET)

    def test_request_after_signing_rejected_but_registered_query_remains(self):
        self.accept_route()
        self.deliver_header()
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertNotIsInstance(publication, PendingBatch)
        # 已登记的同号同参签发后仍可查询
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), RETURNED)
        # 签发后的新单拒绝
        with self.assertRaises(ValueError):
            request_return(self.accounts, 'A', 'REQ-NEW', 'r', (0,))


class BeforePathReturnTests(ProjectTestCase):
    def test_return_before_branch_generated(self):
        self.accept_route()
        self.assertEqual(advance(self.work, self.accounts, 'A').kind, 'header')
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(
            [(row.edge_path, row.path, row.grams, row.category) for row in publication.snapshot.rows],
            [
                (LEFT, ('S', 'L', 'D'), 5, 'D'),
                (TARGET, ('S', 'R'), 7, None),
            ],
        )
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 5})
        self.assertEqual(publication.snapshot.returned_grams, 7)
        self.assertEqual(publication.snapshot.return_request_id, 'REQ-1')
        self.assertEqual(publication.receipt.original_grams, 12)
        self.assertEqual(publication.receipt.detail_count, 2)
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), RETURNED)

    def test_reprint_contains_null_category_return_row(self):
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        self.drain()
        publication = close(self.accounts, 'A')
        receipt_line, detail_line = reprint(
            self.accounts, receipt_id=publication.receipt.receipt_id,
        ).splitlines()
        receipt = json.loads(receipt_line)
        details = json.loads(detail_line)
        self.assertEqual(receipt['original_grams'], 12)
        self.assertEqual(receipt['detail_count'], 2)
        self.assertEqual(details['category_totals'], {'D': 5})
        self.assertEqual(details['returned_grams'], 7)
        self.assertEqual(details['return_request_id'], 'REQ-1')
        self.assertEqual(details['rows'], [
            {'record_id': 'r', 'path': ['S', 'L', 'D'], 'edge_path': [0, 2],
             'grams': 5, 'category': 'D'},
            {'record_id': 'r', 'path': ['S', 'R'], 'edge_path': [1],
             'grams': 7, 'category': None},
        ])


class WaitingReturnTests(ProjectTestCase):
    def test_settle_while_right_relay_paused_then_release(self):
        self.accept_route()
        self.drain(pause=at_event('return', TARGET))
        # 暂停目标回流时仍可办退料
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        for _ in range(20):
            event = advance(self.work, self.accounts, 'A', pause=at_event('return', TARGET))
            if event is None:
                break
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), RETURNED)
        # 放行迟到回流，资格不恢复
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(
            [(row.edge_path, row.grams, row.category) for row in publication.snapshot.rows],
            [(LEFT, 5, 'D'), (TARGET, 7, None)],
        )
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 5})
        self.assertEqual(publication.snapshot.returned_grams, 7)

    def test_unrelated_branch_and_independent_batch_proceed(self):
        self.accept_route()
        accept(self.work, 'B', 'v1', (WeighingRecord('other', 'D', 3),))
        self.drain(pause=at_event('return', TARGET))
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        for _ in range(20):
            if advance(self.work, self.accounts, 'A', pause=at_event('return', TARGET)) is None:
                break
        # 独立批不受影响
        self.drain('B')
        other = close(self.accounts, 'B')
        self.assertEqual(dict(other.snapshot.category_totals), {'D': 3})
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(publication.snapshot.returned_grams, 7)


class InFlightDenialTests(ProjectTestCase):
    def test_result_submitted_even_in_flight_denies_return(self):
        self.accept_route()
        self.drain(pause=at_event('result', RIGHT))
        # 右路终点已在加工端提交，结果在途
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        for _ in range(10):
            event = advance(self.work, self.accounts, 'A', pause=at_event('result', RIGHT))
            if event is None:
                break
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), DENIED)
        # 待核期间不签
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(
            [(row.edge_path, row.grams, row.category) for row in publication.snapshot.rows],
            [(LEFT, 5, 'D'), (RIGHT, 7, 'D')],
        )
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 12})
        self.assertIsNone(publication.snapshot.returned_grams)
        self.assertIsNone(publication.snapshot.return_request_id)
        self.assertEqual(publication.receipt.detail_count, 2)

    def test_descendant_terminal_blocks_ancestor_cut(self):
        self.accept_route()
        self.drain(pause=at_event('result', RIGHT))
        # 选根分支 (1,)，其后代终点 (1,3) 已提交
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        for _ in range(10):
            if advance(self.work, self.accounts, 'A', pause=at_event('result', RIGHT)) is None:
                break
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), DENIED)


class ReturnBoundaryTests(ProjectTestCase):
    def test_zero_gram_parallel_edges_cover_all_paths(self):
        self.accept_route(graph=parallel_zero_graph(),
                          records=(WeighingRecord('r', 'S', 12),))
        advance(self.work, self.accounts, 'A')
        # 退掉边 (1,)（7 克），并暂停零克边 (2,) 的终点，制造缺项待齐
        request_return(self.accounts, 'A', 'REQ-Z', 'r', (1,))
        self.drain(pause=at_event('result', (2,)))
        # 零克边 (2,) 普通终点未到，须待齐
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        view = AccountStore(self.accounts).load_for_close('A')
        self.assertEqual(
            {result.occurrence.key.edge_path for result in view.results}, {(0,)},
        )
        # 零克终点到达后签 D 类 5 克、退料 7 克，保留三条行
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(
            [(row.edge_path, row.grams, row.category) for row in publication.snapshot.rows],
            [((0,), 5, 'D'), ((1,), 7, None), ((2,), 0, 'D')],
        )
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 5})
        self.assertEqual(publication.snapshot.returned_grams, 7)
        self.assertEqual(publication.receipt.original_grams, 12)

    def test_terminal_cut_replaces_single_terminal(self):
        # 直接退终点边序 (1,3)，截点即终点
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-T', 'r', RIGHT)
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(
            [(row.edge_path, row.grams, row.category) for row in publication.snapshot.rows],
            [(LEFT, 5, 'D'), (RIGHT, 7, None)],
        )
        self.assertEqual(publication.snapshot.returned_grams, 7)


class ReturnReliabilityTests(ProjectTestCase):
    def test_return_is_scoped_to_one_record_in_multi_record_batch(self):
        # 两条同构原称，只退 r1 的右路；r2 不受影响
        self.accept_route(records=(WeighingRecord('r1', 'S', 12), WeighingRecord('r2', 'S', 12)))
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'Q-MULTI', 'r1', TARGET)
        self.drain()
        publication = close(self.accounts, 'A')
        rows = [(row.record_id, row.edge_path, row.grams, row.category)
                for row in publication.snapshot.rows]
        self.assertEqual(rows, [
            ('r1', LEFT, 5, 'D'),
            ('r1', TARGET, 7, None),
            ('r2', LEFT, 5, 'D'),
            ('r2', RIGHT, 7, 'D'),
        ])
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 17})
        self.assertEqual(publication.snapshot.returned_grams, 7)
        self.assertEqual(publication.receipt.original_grams, 24)
        self.assertEqual(publication.receipt.detail_count, 4)

    def test_pending_verification_blocks_closure(self):
        # 退料已登记但尚未结清（申请投递被暂停），本批不签
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'Q-PENDING', 'r', TARGET)
        self.assertEqual(
            close(self.accounts, 'A'), PendingBatch('A'),
        )
        self.assertEqual(
            request_return(self.accounts, 'A', 'Q-PENDING', 'r', TARGET), PENDING,
        )

    def test_lost_request_ack_continues_with_same_request(self):
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        with self.assertRaises(AcknowledgmentLost):
            self.drain(lose_ack=at_event('return_request', TARGET))
        self.reopen()
        # 重开后同一请求续办
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), PENDING)
        self.drain()
        publication = close(self.accounts, 'A')
        self.assertEqual(publication.snapshot.returned_grams, 7)
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), RETURNED)

    def test_lost_outcome_ack_settles_once(self):
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        with self.assertRaises(AcknowledgmentLost):
            self.drain(lose_ack=at_event('return_outcome', TARGET))
        self.reopen()
        self.drain()
        first = close(self.accounts, 'A')
        # 重投只产生唯一固定账
        self.assertEqual(close(self.accounts, 'A'), first)
        self.assertEqual(read(self.accounts, batch_id='A'), first)

    def test_reopen_archive_returns_fixed_return_book(self):
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        self.drain()
        publication = close(self.accounts, 'A')
        printout = reprint(self.accounts, batch_id='A')
        receipt_id = publication.receipt.receipt_id
        self.work.close()
        self.accounts.close()
        archive = self.open_database(self.accounts_path, initialize_accounts)
        self.assertEqual(read(archive, receipt_id=receipt_id), publication)
        self.assertEqual(reprint(archive, batch_id='A'), printout)

    def test_overlapping_ordinary_result_rejected_at_accounts(self):
        from accounts.acceptance import accept_result
        from work.lineage import OccurrenceKey, RouteOccurrence
        from work.transform import TerminalResult
        self.accept_route()
        advance(self.work, self.accounts, 'A')
        request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET)
        # 先把退料结清为已退，但暂不封账
        from work.delivery import DeliveryEvent
        for _ in range(20):
            event = advance(self.work, self.accounts, 'A')
            if not isinstance(event, DeliveryEvent) or event.kind == 'completion':
                break
        self.assertEqual(request_return(self.accounts, 'A', 'REQ-1', 'r', TARGET), RETURNED)
        # 已退截点子树上的迟到普通终点必须拒绝入账
        late_key = OccurrenceKey('A', 'r', RIGHT)
        late = TerminalResult(
            RouteOccurrence(late_key, 'v1', 0, ('S', 'R', 'D'), 7), 'D',
        )
        with self.assertRaises(SourceError):
            accept_result(self.accounts, late)
