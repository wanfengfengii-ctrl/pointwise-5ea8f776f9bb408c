import json
from dataclasses import asdict, dataclass

from ports import encode


@dataclass(frozen=True, slots=True)
class DetailRow:
    record_id: str
    path: tuple[str, ...]
    edge_path: tuple[int, ...]
    grams: int
    category: str


@dataclass(frozen=True, slots=True)
class DetailSnapshot:
    rows: tuple[DetailRow, ...]
    category_totals: tuple[tuple[str, int], ...]


def make_detail_snapshot(ordered_results, totals):
    rows = []
    for result in ordered_results:
        occurrence = result.occurrence
        rows.append(DetailRow(
            occurrence.key.record_id, occurrence.path, occurrence.key.edge_path,
            occurrence.grams, result.category,
        ))
    return DetailSnapshot(tuple(rows), tuple(sorted(totals.items())))


def encode_snapshot(snapshot):
    return encode({
        'rows': [asdict(row) for row in snapshot.rows],
        'category_totals': dict(snapshot.category_totals),
    })


def decode_snapshot(payload):
    data = json.loads(payload)
    rows = tuple(DetailRow(
        row['record_id'], tuple(row['path']), tuple(row['edge_path']), row['grams'], row['category'],
    ) for row in data['rows'])
    return DetailSnapshot(rows, tuple(sorted(data['category_totals'].items())))
