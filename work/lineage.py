import json
from dataclasses import dataclass

from ports import encode
from rules.weights import require_grams


class SourceError(ValueError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class OccurrenceKey:
    batch_id: str
    record_id: str
    edge_path: tuple[int, ...]

    def token(self):
        return encode((self.record_id, self.edge_path))

    @classmethod
    def from_token(cls, batch_id, token):
        record_id, edges = json.loads(token)
        return cls(batch_id, record_id, tuple(edges))


@dataclass(frozen=True, slots=True)
class RouteOccurrence:
    key: OccurrenceKey
    revision_id: str
    record_index: int
    path: tuple[str, ...]
    grams: int

    def to_data(self):
        return {
            'batch_id': self.key.batch_id,
            'token': self.key.token(),
            'revision_id': self.revision_id,
            'record_index': self.record_index,
            'path': self.path,
            'grams': self.grams,
        }

    @classmethod
    def from_data(cls, data):
        return cls(
            OccurrenceKey.from_token(data['batch_id'], data['token']), data['revision_id'],
            data['record_index'], tuple(data['path']), data['grams'],
        )


def make_root_occurrence(header, record_index):
    record = header.records[record_index]
    return RouteOccurrence(
        OccurrenceKey(header.batch_id, record.record_id, ()),
        header.revision_id, record_index, (record.start,), record.grams,
    )


def derive_child_occurrence(parent, edge_index, edge, grams):
    return RouteOccurrence(
        OccurrenceKey(parent.key.batch_id, parent.key.record_id, parent.key.edge_path + (edge_index,)),
        parent.revision_id, parent.record_index, parent.path + (edge.target,), grams,
    )


def resolve_path(header, key):
    if key.batch_id != header.batch_id or type(key.edge_path) is not tuple:
        raise SourceError('出现身份与批头不符')
    for position, record in enumerate(header.records):
        if record.record_id == key.record_id:
            break
    else:
        raise SourceError('路径没有对应的原称记录')

    path = [record.start]
    grams = record.grams
    for index in key.edge_path:
        if type(index) is not int or not 0 <= index < len(header.graph.edges):
            raise SourceError('无效冻结边序位')
        edge = header.graph.edges[index]
        if edge.source != path[-1]:
            raise SourceError('路径与冻结边不连续')
        path.append(edge.target)
        grams = edge.grams
    return position, tuple(path), grams


def validate_lineage(occurrence, header):
    position, path, grams = resolve_path(header, occurrence.key)
    try:
        require_grams(occurrence.grams)
    except ValueError as error:
        raise SourceError('交付重量无效') from error
    if (
        occurrence.revision_id != header.revision_id
        or type(occurrence.record_index) is not int
        or occurrence.record_index != position
        or occurrence.path != path
        or occurrence.grams != grams
    ):
        raise SourceError('交付与冻结版本、原序、路径或重量不符')
