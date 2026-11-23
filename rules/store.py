import json

from ports import encode
from rules.routes import RouteGraph


class RuleStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS rule_revisions (
                revision_id TEXT PRIMARY KEY NOT NULL,
                graph TEXT NOT NULL
            )
        ''')

    def write_frozen(self, revision_id, graph):
        self.connection.execute(
            'INSERT INTO rule_revisions (revision_id, graph) VALUES (?, ?)',
            (revision_id, encode(graph.to_data())),
        )

    def read_frozen(self, revision_id):
        row = self.connection.execute(
            'SELECT graph FROM rule_revisions WHERE revision_id = ?', (revision_id,),
        ).fetchone()
        return None if row is None else RouteGraph.from_data(json.loads(row[0]))
