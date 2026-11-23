# 澄桥材料试验中心批次加工档案

白班用冻结的加工路线将称量工位送来的余料拆分和归类。夜班按批领取原路线明细、分类克数和固定回执。加工端和封账端各持有一个 SQLite 文件。封账端兼作派生待办的回流中继，夜班可以在加工端关闭后独立领账。

## 核验与演示

使用 Python 3.11 标准库，从项目根目录运行完整回归。项目控制台物化时提供标准核验入口。

```sh
python3 -B check_project.py
python3 -B demo.py
```

演示在临时目录显式载入 `samples/shift.json`。12 克试片余料拆成左路 5 克、右路 7 克，两路到达 D。演示暂停右路交接，核对该批待完成及独立 3 克批已签，随后恢复、签发并重开档案领取。演示打印实际业务场景、固定回执及明细账。全套回归还覆盖反序到件、零克路径、结果仍在途、确认丢失后重投、真实提交失败及单独重开档案。

## 正常工作流程

1. 白班登记具名路线版本。规则明确列出节点、有序有向边、每条边的固定克数以及终点类别。非法引用、有环路线和已确定的重量不守恒都拒绝冻结。
2. 称量工位提交批次号、规则号和原序记录。首次接纳同时登记批头与全部根待办，返回接纳号。
3. 白班可冻结新版本或重投原单。旧批按原冻结版加工。同号且全部字段和原序相同则返回原接纳号，有效异参抛出 `BatchConflict`。
4. 加工员推进一个待办。加工使用本批冻结的边和类别，分支按固定克数生成子项，终点保留流入克数和原路径。
5. 派生项经封账端中继回流继续加工。暂缓交接或确认遗失可续投。工作完成后加工端交付本批终点范围，各终点结果允许先后独立到达。
6. 封账端按原出现持久接纳结果。同码异路分别入账，同次同载荷重投复用已入账结果，来源错误或同标识异载荷抛出 `SourceError`。
7. 封账时按原记录序位和冻结边序路径排序，逐原记录核对原称并复算分类账。所有结果齐备且核对通过后，一次提交固定回执、明细及领取索引。提交失败抛出 SQLite 错误，回滚本次签发。
8. 夜班按批次号或成果号读取和重印固定成果。重开、再次封账和同参重投后都可领到原成果。

## Python 接口

```python
from contextlib import closing

from accounts.finalize import close, initialize_accounts
from intake.batches import accept, initialize_work
from intake.records import WeighingRecord
from ports import open_port
from records.export import PendingBatch, read, reprint
from rules.revisions import freeze
from rules.routes import RouteEdge, RouteGraph
from work.tasks import advance

graph = RouteGraph(
    ('S', 'L', 'R', 'D'),
    (RouteEdge('S', 'L', 5), RouteEdge('S', 'R', 7),
     RouteEdge('L', 'D', 5), RouteEdge('R', 'D', 7)),
    (('D', 'D'),),
)
with closing(open_port('work.sqlite')) as work, closing(open_port('accounts.sqlite')) as accounts:
    initialize_work(work)
    initialize_accounts(accounts)
    freeze(work, 'morning-1', graph)
    accepted = accept(work, 'batch-12', 'morning-1', [WeighingRecord('r-12', 'S', 12)])
    while advance(work, accounts, accepted.batch_id) is not None:
        pass
    result = close(accounts, accepted.batch_id)
    if not isinstance(result, PendingBatch):
        archived = read(accounts, receipt_id=result.receipt.receipt_id)
        print(dict(archived.snapshot.category_totals))
        print(reprint(accounts, receipt_id=result.receipt.receipt_id))
```

`freeze` 返回 `FrozenRevision`。同号同图可以复领，同号异图抛出 `RevisionConflict`。`accept` 返回 `AcceptedBatch`，未知规则号抛出 `UnknownRevision`。标识为非空文本。记录必须含原记录号、冻结起点和非负整数克数，批内原记录号唯一。空记录序列、空值、布尔、负数、非整数及与起点拆分不符的重量均拒绝。合法零克出现保留为明细，也参与封账资格判断。平行边以冻结边序位区分，明细同时保留节点路径和边序路径。

`advance` 按指定批次推进一步，返回执行的 `OccurrenceKey`、交接的 `DeliveryEvent` 或当前无可推进事件时的 `None`。未知批次抛出 `UnknownBatch`。可选的 `pause(event)` 暂缓匹配交接，`lose_ack(event)` 在匹配事件的接收事务提交后引发 `AcknowledgmentLost`，调用方可以重开并续投。事件类型为 `header`、`child`、`return`、`result`、`completion`、`return_request`、`return_outcome`。批头须先接纳。`None` 表示当前这次调用无法继续推进，是否可领以 `close` 或 `read` 为准。退料交接优先于普通回流交接，暂停目标回流时仍可办退料；退料的接单、结清与料账规则见 RETURNS.md。

`close(accounts, batch_id)` 返回固定 `Publication` 或 `PendingBatch`。`read` 和 `reprint` 用关键字提供一个查询键。尚无可领快照的批次号返回 `PendingBatch`，包括批头尚未到达档案端时。未知成果号抛出 `UnknownReceipt`。重印返回两行 JSON，依次是固定回执和固定明细账。初始化可重复执行并保留用户数据，不会自动载入样例。`demo.load_sample` 逐批使用正常受理规则，同号异参沿用正常冲突语义。
