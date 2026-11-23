from dataclasses import asdict, dataclass

from ports import encode


@dataclass(frozen=True, slots=True)
class PublishedReceipt:
    receipt_id: str
    batch_id: str
    acceptance_id: str
    revision_id: str
    original_grams: int
    detail_count: int


def make_receipt(number, header, detail_count):
    return PublishedReceipt(
        f'R{number:06d}', header.batch_id, header.acceptance_id, header.revision_id,
        sum(record.grams for record in header.records), detail_count,
    )


def render_receipt(receipt):
    return encode(asdict(receipt))
