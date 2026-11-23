import json
from dataclasses import replace

from accounts.acceptance import accept_result
from accounts.finalize import close
from accounts.store import AccountStore
from intake.batches import accept
from intake.records import WeighingRecord
from records.export import PendingBatch, read
from rules.routes import RouteEdge, RouteGraph
from tests.fixtures import LEFT, RIGHT, ProjectTestCase, at_event, route_graph
from work.delivery import AcknowledgmentLost
from work.journal import WorkJournal
from work.lineage import SourceError


class ProcessingTests(ProjectTestCase):
    def test_distinct_routes_to_same_category(self):
        self.accept_route()
        self.drain(pause=at_event('result', LEFT))
        view = AccountStore(self.accounts).load_for_close('A')
        self.assertEqual([result.occurrence.grams for result in view.results], [7])
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        self.drain()
        view = AccountStore(self.accounts).load_for_close('A')
        arrival = [list(result.occurrence.key.edge_path) for result in view.results]
        self.assertEqual(arrival, [[1, 3], [0, 2]])
        publication = close(self.accounts, 'A')
        self.assert_route_book(publication)
        rows = publication.snapshot.rows
        self.assertEqual(sum(row.grams for row in rows if row.category == 'D'), 12)
        self.assertEqual(dict(publication.snapshot.category_totals), {'D': 12})
        path_and_grams = [[list(row.path), row.grams] for row in rows]
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'v4.reverse_arrival',
            'input': {'record': 'r', 'grams': 12, 'result_arrival': arrival},
            'expected': [[['S', 'L', 'D'], 5], [['S', 'R', 'D'], 7]],
            'actual': path_and_grams,
            'downstream_expected': {'D': 12},
            'downstream_actual': dict(publication.snapshot.category_totals),
            'code_paths': ['work/transform.py', 'accounts/totals.py', 'accounts/finalize.py'],
            'oracle': '冻结边 0、2 组成左路且为 5 克，边 1、3 组成右路且为 7 克。左路冻结边序更早，分类账应为 5 加 7。',
        }, ensure_ascii=False, sort_keys=True))

    def test_paused_handoff_keeps_only_affected_batch_open(self):
        self.accept_route()
        accept(self.work, 'B', 'v1', (WeighingRecord('other', 'D', 3),))
        self.drain(pause=at_event('child', (1,)))
        self.drain('B')
        self.reopen()
        left = AccountStore(self.accounts).load_for_close('A').results
        self.assertEqual([(result.occurrence.key.edge_path, result.occurrence.grams) for result in left], [(LEFT, 5)])
        self.assertIsNone(WorkJournal(self.work).completion_view('A'))
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        self.assertEqual(read(self.accounts, batch_id='A'), PendingBatch('A'))
        other = close(self.accounts, 'B')
        self.assertEqual(dict(other.snapshot.category_totals), {'D': 3})
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'))
        self.assertEqual(read(self.accounts, batch_id='B'), other)

    def test_zero_weight_branch_keeps_batch_open(self):
        self.accept_route(graph=route_graph(12, 0))
        self.drain(pause=at_event('child', (1,)))
        results = AccountStore(self.accounts).load_for_close('A').results
        self.assertEqual([result.occurrence.grams for result in results], [12])
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'), left=12, right=0)

    def test_result_in_transit(self):
        self.accept_route()
        self.drain(pause=at_event('result', RIGHT))
        view = AccountStore(self.accounts).load_for_close('A')
        self.assertEqual({key.edge_path for key in view.completion.result_keys}, {LEFT, RIGHT})
        self.assertEqual([result.occurrence.key.edge_path for result in view.results], [LEFT])
        self.assertEqual(close(self.accounts, 'A'), PendingBatch('A'))
        self.reopen()
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'))

    def test_lost_delivery_ack(self):
        self.accept_route()
        with self.assertRaises(AcknowledgmentLost) as interruption:
            self.drain(pause=at_event('child', (1,)), lose_ack=at_event('result', LEFT))
        self.assertEqual(interruption.exception.event.key.edge_path, LEFT)
        self.reopen()
        before = AccountStore(self.accounts).load_for_close('A').results
        self.assertEqual([(row.occurrence.grams, row.occurrence.path) for row in before], [(5, ('S', 'L', 'D'))])
        self.assertIn('result', [entry.kind for entry in WorkJournal(self.work).pending_deliveries('A')])
        self.drain()
        publication = close(self.accounts, 'A')
        self.assert_route_book(publication)
        self.assertFalse(accept_result(self.accounts, before[0]))
        self.assertEqual(close(self.accounts, 'A'), publication)

    def test_conflicting_replay_keeps_original_result(self):
        self.accept_route()
        with self.assertRaises(AcknowledgmentLost):
            self.drain(pause=at_event('child', (1,)), lose_ack=at_event('result', LEFT))
        self.reopen()
        store = AccountStore(self.accounts)
        original, = store.load_for_close('A').results
        replacements = (
            replace(original, category='other'),
            replace(original, occurrence=replace(original.occurrence, revision_id='other')),
            replace(original, occurrence=replace(original.occurrence, grams=7)),
        )
        for changed in replacements:
            with self.assertRaises(SourceError):
                accept_result(self.accounts, changed)
            self.assertEqual(store.load_for_close('A').results, (original,))
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'))

    def test_relay_replays_after_either_ack_is_lost(self):
        for kind in ('child', 'return'):
            with self.subTest(kind=kind):
                self.accept_route(kind)
                with self.assertRaises(AcknowledgmentLost):
                    self.drain(kind, lose_ack=at_event(kind, (0,)))
                self.reopen()
                self.drain(kind)
                self.assert_route_book(close(self.accounts, kind))
                count, = self.accounts.execute('''
                    SELECT COUNT(*) FROM relay_occurrences WHERE batch_id = ? AND returned = 1
                ''', (kind,)).fetchone()
                self.assertEqual(count, 4)

    def test_parallel_edges_have_distinct_edge_paths(self):
        graph = RouteGraph(
            ('S', 'D'), (RouteEdge('S', 'D', 0), RouteEdge('S', 'D', 5)), (('D', 'D'),),
        )
        records = (WeighingRecord('r', 'S', 5),)
        self.accept_route(graph=graph, records=records)
        self.drain()
        snapshot = close(self.accounts, 'A').snapshot
        self.assertEqual([(row.path, row.edge_path, row.grams) for row in snapshot.rows], [
            (('S', 'D'), (0,), 0), (('S', 'D'), (1,), 5),
        ])
        self.assertEqual(dict(snapshot.category_totals), {'D': 5})
