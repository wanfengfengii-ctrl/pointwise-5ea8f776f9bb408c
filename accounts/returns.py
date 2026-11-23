import json
from dataclasses import dataclass

from accounts.store import AccountStore
from ports import encode, transaction
from rules.routes import require_name
from work.lineage import OccurrenceKey, ReturnRequest, SourceError, resolve_path, subtree_contains


PENDING = 'pending'
RETURNED = 'returned'
DENIED = 'denied'


@dataclass(frozen=True, slots=True)
class ReturnRecord:
    request_id: str
    batch_id: str
    record_id: str
    edge_path: tuple[int, ...]
    status: str
    grams: int

    def to_request(self):
        return ReturnRequest(self.batch_id, self.request_id, self.record_id, self.edge_path)


class ReturnStore:
    def __init__(self, connection):
        self.connection = connection

    def initialize(self):
        self.connection.execute('''
            CREATE TABLE IF NOT EXISTS return_requests (
                request_id TEXT PRIMARY KEY NOT NULL,
                batch_id TEXT NOT NULL UNIQUE REFERENCES account_batches(batch_id),
                record_id TEXT NOT NULL,
                edge_path TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'returned', 'denied')),
                grams INTEGER NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1))
            )
        ''')

    def find_by_batch(self, batch_id):
        row = self.connection.execute('''
            SELECT request_id, record_id, edge_path, status, grams
            FROM return_requests WHERE batch_id = ?
        ''', (batch_id,)).fetchone()
        if row is None:
            return None
        request_id, record_id, edge_path, status, grams = row
        return ReturnRecord(request_id, batch_id, record_id, tuple(json.loads(edge_path)), status, grams)

    def find(self, request_id):
        row = self.connection.execute('''
            SELECT batch_id, record_id, edge_path, status, grams
            FROM return_requests WHERE request_id = ?
        ''', (request_id,)).fetchone()
        if row is None:
            return None
        batch_id, record_id, edge_path, status, grams = row
        return ReturnRecord(request_id, batch_id, record_id, tuple(json.loads(edge_path)), status, grams)

    def insert(self, record):
        self.connection.execute('''
            INSERT INTO return_requests
                (request_id, batch_id, record_id, edge_path, status, grams, confirmed)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        ''', (
            record.request_id, record.batch_id, record.record_id,
            encode(list(record.edge_path)), record.status, record.grams,
        ))

    def confirm_delivery(self, batch_id, request_id):
        changed = self.connection.execute('''
            UPDATE return_requests SET confirmed = 1
            WHERE batch_id = ? AND request_id = ? AND confirmed = 0
        ''', (batch_id, request_id)).rowcount
        if changed != 1:
            raise SourceError('退料申请确认与原投递不符')

    def pending_deliveries(self, batch_id):
        rows = self.connection.execute('''
            SELECT request_id, batch_id, record_id, edge_path, status, grams
            FROM return_requests WHERE batch_id = ? AND confirmed = 0 ORDER BY rowid
        ''', (batch_id,))
        return tuple(
            ReturnRecord(row[0], row[1], row[2], tuple(json.loads(row[3])), row[4], row[5])
            for row in rows
        )

    def save_outcome(self, batch_id, request_id, verdict):
        if verdict not in (RETURNED, DENIED):
            raise SourceError('退料结清依据状态无效')
        record = self.find_by_batch(batch_id)
        if record is None or record.request_id != request_id:
            raise SourceError('退料结清依据与原单号或批头不符')
        if record.status in (RETURNED, DENIED):
            if record.status != verdict:
                raise SourceError('退料结清依据与已确认事实矛盾')
            return False
        changed = self.connection.execute('''
            UPDATE return_requests SET status = ?
            WHERE batch_id = ? AND request_id = ? AND status = 'pending'
        ''', (verdict, batch_id, request_id)).rowcount
        if changed != 1:
            raise SourceError('退料结清依据与原单不符')
        return True


def _validate_edge_path(edge_path):
    if type(edge_path) is not tuple or not edge_path:
        raise ValueError('退料路径须为非空冻结边序元组')
    for index in edge_path:
        if type(index) is not int or isinstance(index, bool):
            raise ValueError('冻结边序位须为整数')
    return edge_path


def request_return(connection, batch_id, request_id, record_id, edge_path):
    require_name(batch_id, '批次号')
    require_name(request_id, '退料单号')
    require_name(record_id, '原称记录号')
    _validate_edge_path(edge_path)
    store = ReturnStore(connection)
    with transaction(connection):
        header = AccountStore(connection).require_header(batch_id)
        if not any(record.record_id == record_id for record in header.records):
            raise ValueError(f'批次 {batch_id} 没有原称记录 {record_id}')
        cut_key = OccurrenceKey(batch_id, record_id, edge_path)
        try:
            _, _, cut_grams = resolve_path(header, cut_key)
        except SourceError as error:
            raise ValueError('退料路径不是有效的冻结路径') from error
        same_id = store.find(request_id)
        if same_id is not None:
            if (same_id.batch_id, same_id.record_id, same_id.edge_path) != (
                batch_id, record_id, edge_path
            ):
                raise ValueError('同单号退料申请参数不一致')
            # 已登记的同号同参在签发后仍可查询，直接回现状。
            return same_id.status
        # 仅对“新单”检查签发状态与每批一单约束。
        if AccountStore(connection)._batch_facts(batch_id)[2] is not None:
            raise ValueError('已签发批次不能新增退料单')
        same_batch = store.find_by_batch(batch_id)
        if same_batch is not None:
            raise ValueError('每批只受理一张退料单')
        record = ReturnRecord(request_id, batch_id, record_id, edge_path, PENDING, cut_grams)
        store.insert(record)
    return PENDING


def accept_return_outcome(connection, payload):
    request = ReturnRequest.from_data(payload)
    verdict = payload.get('verdict')
    if verdict not in (RETURNED, DENIED):
        raise SourceError('退料结清依据状态无效')
    store = ReturnStore(connection)
    with transaction(connection):
        header = AccountStore(connection).require_header(request.batch_id)
        record = store.find_by_batch(request.batch_id)
        if record is None or record.request_id != request.request_id:
            raise SourceError('退料结清依据没有对应原单')
        if record.record_id != request.record_id or record.edge_path != request.edge_path:
            raise SourceError('退料结清依据与所选出现不符')
        try:
            resolve_path(header, OccurrenceKey(request.batch_id, request.record_id, request.edge_path))
        except SourceError as error:
            raise SourceError('退料结清依据与冻结路径不符') from error
        if verdict == RETURNED:
            results = AccountStore(connection).load_for_close(request.batch_id).results
            for result in results:
                key = result.occurrence.key
                if key.record_id == request.record_id and subtree_contains(key.edge_path, request.edge_path):
                    raise SourceError('退料截点与已入账普通终点重叠')
        store.save_outcome(request.batch_id, request.request_id, verdict)
