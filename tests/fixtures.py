import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from accounts.finalize import initialize_accounts
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from ports import open_port
from rules.revisions import freeze
from rules.routes import RouteEdge, RouteGraph
from work.tasks import advance


LEFT = (0, 2)
RIGHT = (1, 3)


def route_graph(left=5, right=7, category='D'):
    return RouteGraph(
        ('S', 'L', 'R', 'D'),
        (RouteEdge('S', 'L', left), RouteEdge('S', 'R', right),
         RouteEdge('L', 'D', left), RouteEdge('R', 'D', right)),
        (('D', category),),
    )


def at_event(kind, path):
    def matches(event):
        return event.kind == kind and event.key is not None and event.key.edge_path == path
    return matches


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(TemporaryDirectory(prefix='chengqiao-test-')))
        self.work_path = self.folder / 'work.sqlite'
        self.accounts_path = self.folder / 'accounts.sqlite'
        self.work = self.open_database(self.work_path, initialize_work)
        self.accounts = self.open_database(self.accounts_path, initialize_accounts)

    def open_database(self, path, initialize):
        connection = open_port(path)
        self.addCleanup(connection.close)
        initialize(connection)
        return connection

    def reopen(self):
        self.work.close()
        self.accounts.close()
        self.work = self.open_database(self.work_path, initialize_work)
        self.accounts = self.open_database(self.accounts_path, initialize_accounts)

    def accept_route(self, batch_id='A', *, revision_id='v1', graph=None, records=None):
        freeze(self.work, revision_id, route_graph() if graph is None else graph)
        if records is None:
            records = (WeighingRecord('r', 'S', 12),)
        return accept(self.work, batch_id, revision_id, records)

    def drain(self, batch_id='A', **controls):
        for _ in range(200):
            if advance(self.work, self.accounts, batch_id, **controls) is None:
                return
        self.fail(f'有限样例 {batch_id} 未在预期步数内停止推进')

    def assert_route_book(self, publication, *, left=5, right=7, category='D'):
        self.assertEqual([
            (row.record_id, row.path, row.edge_path, row.grams, row.category)
            for row in publication.snapshot.rows
        ], [
            ('r', ('S', 'L', 'D'), LEFT, left, category),
            ('r', ('S', 'R', 'D'), RIGHT, right, category),
        ])
        self.assertEqual(dict(publication.snapshot.category_totals), {category: left + right})
        self.assertEqual(publication.receipt.original_grams, left + right)
        self.assertEqual(publication.receipt.detail_count, 2)
