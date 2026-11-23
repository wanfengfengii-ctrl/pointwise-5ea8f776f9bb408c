import json
from contextlib import closing

from accounts.finalize import close, initialize_accounts
from accounts.store import AccountStore
from demo import load_sample
from intake.batches import initialize_work
from ports import open_port
from records.export import PendingBatch
from work.journal import RelayJournal, WorkJournal
from work.lineage import OccurrenceKey
from work.tasks import advance


BATCH = "day-012"
RECORD = "sample-12"
TARGET = (1,)
LEFT = (0, 2)
RIGHT = (1, 3)


def drain(work, accounts, batch_id=BATCH, **controls):
    for _ in range(200):
        if advance(work, accounts, batch_id, **controls) is None:
            return
    raise RuntimeError(f"{batch_id} 的样例推进未收敛")


def prepare_case(work, accounts, phase):
    """在新初始化的两个端口上装入原样例，并推进至指定人工接单时刻。"""
    if phase not in ("before-path", "waiting-return", "result-in-flight"):
        raise ValueError(f"未知样例时刻 {phase}")
    load_sample(work)
    if phase == "before-path":
        event = advance(work, accounts, BATCH)
        if getattr(event, "kind", None) != "header":
            raise RuntimeError("首步未交付批头")
        return

    def pause(event):
        if event.kind == "completion":
            return True
        if phase == "waiting-return":
            return event.kind == "return" and event.key.edge_path == TARGET
        return event.kind == "result" and event.key.edge_path == RIGHT

    drain(work, accounts, pause=pause)


def inspect_v1(work, accounts):
    view = AccountStore(accounts).load_for_close(BATCH)
    right_relay = OccurrenceKey(BATCH, RECORD, TARGET).token()
    right_result = OccurrenceKey(BATCH, RECORD, RIGHT).token()
    return {
        "account_rows": [(row.occurrence.key.edge_path, row.occurrence.grams) for row in view.results],
        "account_has_completion": view.completion is not None,
        "right_return_waiting": any(entry.token == right_relay for entry in RelayJournal(accounts).pending_returns(BATCH)),
        "right_result_waiting": any(entry.kind == "result" and entry.token == right_result
                                    for entry in WorkJournal(work).pending_deliveries(BATCH)),
    }


def main():
    for phase in ("before-path", "waiting-return", "result-in-flight"):
        with closing(open_port(":memory:")) as work, closing(open_port(":memory:")) as accounts:
            initialize_work(work)
            initialize_accounts(accounts)
            prepare_case(work, accounts, phase)
            observed = inspect_v1(work, accounts)
            expected = {
                "account_rows": [] if phase == "before-path" else [(LEFT, 5)],
                "account_has_completion": False,
                "right_return_waiting": phase == "waiting-return",
                "right_result_waiting": phase == "result-in-flight",
            }
            if observed != expected or close(accounts, BATCH) != PendingBatch(BATCH):
                raise SystemExit(f"{phase}：V1 接单前状态错误，实得 {observed}，预期 {expected}")
            drain(work, accounts, "day-ready")
            independent = close(accounts, "day-ready")
            if isinstance(independent, PendingBatch) or dict(independent.snapshot.category_totals) != {"D": 3}:
                raise SystemExit(f"{phase}：独立批未签 D 类 3 克")
            drain(work, accounts)
            publication = close(accounts, BATCH)
            if isinstance(publication, PendingBatch):
                raise SystemExit(f"{phase}：V1 恢复投递后仍待签")
            rows = [(row.edge_path, row.grams, row.category) for row in publication.snapshot.rows]
            totals = dict(publication.snapshot.category_totals)
            if rows != [(LEFT, 5, "D"), (RIGHT, 7, "D")] or totals != {"D": 12}:
                raise SystemExit(f"{phase}：V1 原账错误，实得 {rows}、{totals}")
            print(json.dumps({"phase": phase, **observed, "ordinary_totals": totals}, ensure_ascii=False))


if __name__ == "__main__":
    main()
