from dataclasses import dataclass

from ports import transaction
from rules.routes import RouteGraph, require_name
from rules.store import RuleStore
from rules.validation import validate_revision


class UnknownRevision(ValueError):
    pass


class RevisionConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FrozenRevision:
    revision_id: str
    graph: RouteGraph


def freeze(connection, revision_id, graph):
    require_name(revision_id, '冻结版本号')
    validate_revision(graph)
    store = RuleStore(connection)
    with transaction(connection):
        previous = store.read_frozen(revision_id)
        if previous is None:
            store.write_frozen(revision_id, graph)
        elif previous != graph:
            raise RevisionConflict(f'冻结版本 {revision_id} 已绑定其他内容')
    return FrozenRevision(revision_id, graph)


def get_frozen_revision(connection, revision_id):
    require_name(revision_id, '冻结版本号')
    graph = RuleStore(connection).read_frozen(revision_id)
    if graph is None:
        raise UnknownRevision(revision_id)
    return FrozenRevision(revision_id, graph)
