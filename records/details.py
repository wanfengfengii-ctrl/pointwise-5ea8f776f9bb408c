import json
from dataclasses import asdict, dataclass

from ports import encode


@dataclass(frozen=True, slots=True)
class DetailRow:
    record_id: str
    path: tuple[str, ...]
    edge_path: tuple[int, ...]
    grams: int
    category: str | None


@dataclass(frozen=True, slots=True)
class DetailSnapshot:
    rows: tuple[DetailRow, ...]
    category_totals: tuple[tuple[str, int], ...]
    returned_grams: int | None = None
    return_request_id: str | None = None


def make_detail_snapshot(ordered_results, totals, *, returned_grams=None, return_request_id=None):
    rows = []
    for row in ordered_results:
        rows.append(row if isinstance(row, DetailRow) else DetailRow(
            row.occurrence.key.record_id, row.occurrence.path, row.occurrence.key.edge_path,
            row.occurrence.grams, row.category,
        ))
    return DetailSnapshot(
        tuple(rows), tuple(sorted(totals.items())), returned_grams, return_request_id,
    )


def encode_snapshot(snapshot):
    payload = {
        'rows': [asdict(row) for row in snapshot.rows],
        'category_totals': dict(snapshot.category_totals),
    }
    if snapshot.returned_grams is not None:
        payload['returned_grams'] = snapshot.returned_grams
        payload['return_request_id'] = snapshot.return_request_id
    return encode(payload)


def decode_snapshot(payload):
    data = json.loads(payload)
    rows = tuple(DetailRow(
        row['record_id'], tuple(row['path']), tuple(row['edge_path']), row['grams'], row['category'],
    ) for row in data['rows'])
    return DetailSnapshot(
        rows, tuple(sorted(data['category_totals'].items())),
        data.get('returned_grams'), data.get('return_request_id'),
    )
