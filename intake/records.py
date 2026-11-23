from dataclasses import dataclass

from rules.routes import require_name
from rules.weights import require_grams, split_weights


@dataclass(frozen=True, slots=True)
class WeighingRecord:
    record_id: str
    start: str
    grams: int


def canonical_records(records):
    try:
        ordered = tuple(records)
    except TypeError as error:
        raise ValueError('原称须为有序记录序列') from error
    if not ordered:
        raise ValueError('批次至少需要一条原称记录')
    seen = set()
    for record in ordered:
        if not isinstance(record, WeighingRecord):
            raise ValueError('原称须包含记录号、起点和重量')
        require_name(record.record_id, '原记录号')
        require_name(record.start, '称量起点')
        require_grams(record.grams)
        if record.record_id in seen:
            raise ValueError(f'重复原记录 {record.record_id}')
        seen.add(record.record_id)
    return ordered


def validate_records(records, graph):
    ordered = canonical_records(records)
    for record in ordered:
        if record.start not in graph.nodes:
            raise ValueError(f'未知称量起点 {record.start}')
        outgoing = graph.outgoing_edges(record.start)
        if outgoing:
            split_weights(record.grams, outgoing)
    return ordered
