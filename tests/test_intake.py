import json
from dataclasses import replace

from accounts.finalize import close, initialize_accounts
from demo import SAMPLE, load_sample
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from intake.retries import BatchConflict
from intake.store import IntakeStore
from rules.revisions import RevisionConflict, UnknownRevision, freeze, get_frozen_revision
from rules.routes import RouteEdge, RouteGraph
from tests.fixtures import ProjectTestCase, route_graph


class IntakeTests(ProjectTestCase):
    def test_frozen_rule_survives_revision(self):
        self.accept_route('old', revision_id='shift-1')
        self.accept_route('new', revision_id='shift-2', graph=route_graph(category='E'))
        self.reopen()
        self.drain('new')
        self.drain('old')
        old = close(self.accounts, 'old')
        new = close(self.accounts, 'new')
        self.assert_route_book(old)
        self.assert_route_book(new, category='E')
        self.assertEqual((old.receipt.revision_id, new.receipt.revision_id), ('shift-1', 'shift-2'))

    def test_initialize_existing_and_replay_sample(self):
        first = load_sample(self.work)
        self.accept_route('user-batch', revision_id='user-rule', graph=route_graph(category='E'))
        self.drain('user-batch')
        unchanged = close(self.accounts, 'user-batch')
        initialize_work(self.work)
        initialize_accounts(self.accounts)
        self.reopen()
        self.assertEqual(load_sample(self.work), first)
        self.assertEqual(get_frozen_revision(self.work, 'user-rule').graph, route_graph(category='E'))
        self.assertEqual(close(self.accounts, 'user-batch'), unchanged)
        data = json.loads(SAMPLE.read_text(encoding='utf-8'))
        data['batches'][1]['records'][0]['grams'] = 4
        edited = self.folder / 'edited-sample.json'
        edited.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(BatchConflict):
            load_sample(self.work, edited)
        self.assertEqual(load_sample(self.work), first)
        self.drain('day-ready')
        self.assertEqual(dict(close(self.accounts, 'day-ready').snapshot.category_totals), {'D': 3})

    def test_reject_cyclic_rule(self):
        original = freeze(self.work, 'valid', route_graph())
        cyclic = RouteGraph(
            ('A', 'B', 'D'),
            (RouteEdge('A', 'B', 3), RouteEdge('B', 'A', 3), RouteEdge('A', 'D', 0)),
            (('D', 'D'),),
        )
        with self.assertRaisesRegex(ValueError, '环'):
            freeze(self.work, 'cycle', cyclic)
        with self.assertRaises(UnknownRevision):
            get_frozen_revision(self.work, 'cycle')
        self.assertEqual(get_frozen_revision(self.work, 'valid'), original)
        self.accept_route('A', revision_id='valid')
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'))

    def test_rule_boundaries(self):
        graph = route_graph()
        freeze(self.work, 'valid', graph)
        invalid = (
            replace(graph, nodes=('S', 'L', 'D')),
            replace(graph, edges=(RouteEdge('S', 'L', 6), *graph.edges[1:])),
            replace(graph, edges=(*graph.edges[:3], RouteEdge('R', 'D', True))),
            replace(graph, terminals=()),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    freeze(self.work, 'invalid', candidate)
                with self.assertRaises(UnknownRevision):
                    get_frozen_revision(self.work, 'invalid')
        with self.assertRaises(RevisionConflict):
            freeze(self.work, 'valid', route_graph(category='E'))
        self.assertEqual(get_frozen_revision(self.work, 'valid').graph, graph)

    def test_replayed_batch(self):
        records = (WeighingRecord('z', 'D', 17), WeighingRecord('a', 'D', 2))
        accepted = self.accept_route(records=records)
        self.reopen()
        self.assertEqual(accept(self.work, 'A', 'v1', records), accepted)
        freeze(self.work, 'v2', route_graph())
        changes = (
            ('v1', (WeighingRecord('z', 'D', 18), records[1])),
            ('v1', tuple(reversed(records))),
            ('v1', (WeighingRecord('z', 'S', 12), records[1])),
            ('v1', (WeighingRecord('other', 'D', 17), records[1])),
            ('v2', records),
        )
        for revision_id, changed in changes:
            with self.subTest(revision_id=revision_id, changed=changed):
                with self.assertRaises(BatchConflict):
                    accept(self.work, 'A', revision_id, changed)
        self.assertEqual(IntakeStore(self.work).find_batch('A').records, records)
        count, = self.work.execute('SELECT COUNT(*) FROM work_occurrences WHERE batch_id = ?', ('A',)).fetchone()
        self.assertEqual(count, 2)
        self.drain()
        snapshot = close(self.accounts, 'A').snapshot
        self.assertEqual([(row.record_id, row.grams) for row in snapshot.rows], [('z', 17), ('a', 2)])
        self.assertEqual(dict(snapshot.category_totals), {'D': 19})

    def test_invalid_weighing_leaves_no_intake(self):
        freeze(self.work, 'v1', route_graph())
        invalid = [(), ({'record_id': 'r', 'start': 'D'},), (WeighingRecord('', 'D', 1),)]
        invalid.extend((WeighingRecord('r', 'D', grams),) for grams in (None, -1, 1.5, True))
        invalid.extend(((WeighingRecord('r', 'unknown', 4),), (WeighingRecord('r', 'S', 11),)))
        invalid.append((WeighingRecord('r', 'D', 1), WeighingRecord('r', 'D', 2)))
        for records in invalid:
            with self.subTest(records=records):
                with self.assertRaises(ValueError):
                    accept(self.work, 'invalid', 'v1', records)
        self.assertIsNone(IntakeStore(self.work).find_batch('invalid'))
        self.assertEqual(self.work.execute('SELECT COUNT(*) FROM work_occurrences').fetchone(), (0,))

    def test_ports_are_independent(self):
        with self.assertRaisesRegex(ValueError, '独立'):
            initialize_accounts(self.work)
        self.accept_route()
        self.drain()
        self.assert_route_book(close(self.accounts, 'A'))
