from __future__ import annotations
import sqlite3
from datetime import datetime, timezone

from app.db import connect
from app.services.audit import emit_audit
from app.services.reconcile_service import check_seal_evidence

# 全局架构口径：工单级原子提交。
# 一次工单 attempt = 一个数据库事务：逐日租约、证据校验、批次封印、写闸生效、
# 审计、工单/attempt/逐日结果全部同事务落库；任一日失败 → 整单 ROLLBACK，
# 所有日恢复提交前写闸与批态，随后用独立事务落「失败工单 + 失败日清单」回执。
# 因此不存在部分日已关闸却无对应 sealed 批的中间态。


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _DayRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _normalize_days(days: list[str]) -> list[str]:
    out = sorted({(d or "").strip() for d in days if (d or "").strip()})
    if not out:
        raise ValueError("结案工单至少勾选一个到货日")
    for d in out:
        if len(d) != 10 or d[4] != "-" or d[7] != "-":
            raise ValueError(f"到货日格式应为 YYYY-MM-DD：{d}")
    return out


def _next_order_no(conn: sqlite3.Connection) -> str:
    # 必须在插入工单行的同一事务内调用：序号随事务提交/回滚，并发工单不会撞单号。
    conn.execute(
        "INSERT INTO settings(key, value) VALUES ('close_order_seq', '0') ON CONFLICT(key) DO NOTHING"
    )
    conn.execute(
        "UPDATE settings SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='close_order_seq'"
    )
    seq = conn.execute("SELECT value FROM settings WHERE key='close_order_seq'").fetchone()["value"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"CO-{stamp}-{int(seq):04d}"


def _attempt_day(
    conn: sqlite3.Connection, order_id: int, attempt_id: int, dock_day: str, now: str
) -> dict:
    """工单事务内处理一个到货日：租约 → 证据 → 封印 → 逐日结果。失败不抛出，记入结果。"""
    try:
        # 1. 逐日租约：held 每日全局唯一，并发工单在此互斥，杜绝双封同日。
        try:
            conn.execute(
                "INSERT INTO day_leases(dock_day, order_id, attempt_id, state, acquired_at) VALUES (?,?,?,?,?)",
                (dock_day, order_id, attempt_id, "held", now),
            )
        except sqlite3.IntegrityError:
            raise _DayRejected("租约冲突：另一工单正在处理该到货日")
        # 2. 结案证据：open 批次存在、差额清零、快照未过期。
        try:
            batch = check_seal_evidence(conn, dock_day)
        except ValueError as e:
            raise _DayRejected(str(e))
        # 3. 封印：批次 open→sealed，写闸即由此派生（同事务，绝不出现关闸无批）。
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed', sealed_at=? WHERE id=?",
            (now, batch["id"]),
        )
        # 4. 租约转封印留痕 + 逐日结果 + 审计，全部在同一事务。
        conn.execute(
            "UPDATE day_leases SET state='converted', released_at=? WHERE dock_day=? AND state='held'",
            (now, dock_day),
        )
        conn.execute(
            '''INSERT INTO close_order_days(order_id, attempt_id, dock_day, batch_id, status, reason)
               VALUES (?,?,?,?,?,NULL)''',
            (order_id, attempt_id, dock_day, batch["id"], "sealed"),
        )
        emit_audit(
            conn, "day_sealed", dock_day=dock_day, order_id=order_id, attempt_id=attempt_id,
            detail=f"工单封印，批次 {batch['batch_no']}",
        )
        return {
            "dock_day": dock_day, "status": "sealed",
            "batch_id": batch["id"], "batch_no": batch["batch_no"], "reason": None,
        }
    except _DayRejected as e:
        return {"dock_day": dock_day, "status": "rejected", "batch_id": None, "batch_no": None, "reason": e.reason}


def _receipt(order_id, order_no, attempt_id, attempt_no, status, results, error):
    return {
        "order_id": order_id,
        "order_no": order_no,
        "attempt_id": attempt_id,
        "attempt_no": attempt_no,
        "status": status,
        "sealed_count": sum(1 for r in results if r["status"] == "sealed"),
        "failed_days": [r for r in results if r["status"] == "rejected"],
        "days": results,
        "error": error,
    }


def _record_failure(
    conn: sqlite3.Connection,
    order_id: int | None,
    attempt_no: int,
    days: list[str],
    results: list[dict],
    error: str,
    note: str | None,
) -> dict:
    """整单回滚后，用独立事务落失败回执：工单、attempt、逐日结果、审计。"""
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    if order_id is None:
        # 工单行随业务事务回滚了，这里重新落一行 failed 工单；
        # 单号在本事务内重新领取（与回滚事务拿到的号一致，因为序号也回滚了）。
        order_no = _next_order_no(conn)
        cur = conn.execute(
            '''INSERT INTO close_orders(order_no, status, day_count, sealed_count, created_at, finished_at, note)
               VALUES (?,?,?,?,?,?,?)''',
            (order_no, "failed", len(days), 0, now, now, note),
        )
        order_id = int(cur.lastrowid)
    else:
        order_no = conn.execute(
            "SELECT order_no FROM close_orders WHERE id=?", (order_id,)
        ).fetchone()["order_no"]
        conn.execute(
            "UPDATE close_orders SET status='failed', sealed_count=0, finished_at=? WHERE id=?",
            (now, order_id),
        )
    cur = conn.execute(
        '''INSERT INTO close_order_attempts(order_id, attempt_no, status, error, started_at, finished_at)
           VALUES (?,?,?,?,?,?)''',
        (order_id, attempt_no, "failed", error, now, now),
    )
    attempt_id = int(cur.lastrowid)
    final_results = []
    for r in results:
        if r["status"] == "rejected":
            status, reason = "rejected", r["reason"]
        else:
            status, reason = "rolled_back", "工单内其他到货日失败，整单回滚"
        conn.execute(
            '''INSERT INTO close_order_days(order_id, attempt_id, dock_day, batch_id, status, reason)
               VALUES (?,?,?,?,?,?)''',
            (order_id, attempt_id, r["dock_day"], r.get("batch_id"), status, reason),
        )
        emit_audit(
            conn, "day_rejected" if status == "rejected" else "day_rolled_back",
            dock_day=r["dock_day"], order_id=order_id, attempt_id=attempt_id, detail=reason,
        )
        final_results.append({**r, "status": status, "reason": reason})
    emit_audit(conn, "order_failed", order_id=order_id, attempt_id=attempt_id, detail=error)
    conn.commit()
    return _receipt(order_id, order_no, attempt_id, attempt_no, "failed", final_results, error)


def _run_attempt(
    conn: sqlite3.Connection,
    order_id: int | None,
    attempt_no: int,
    days: list[str],
    note: str | None,
) -> dict:
    """执行一次工单 attempt：事务边界 = 整单。任一日 rejected → 整单回滚。

    order_id 为 None 表示新工单（工单行在本事务内临时创建，回滚即消失，
    失败时由 _record_failure 重新落 failed 行）；否则为既有失败工单的重试，
    工单行已持久化，回滚不影响它。
    """
    persisted_order_id = order_id
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    if order_id is None:
        order_no = _next_order_no(conn)
        cur = conn.execute(
            '''INSERT INTO close_orders(order_no, status, day_count, sealed_count, created_at, note)
               VALUES (?,?,?,?,?,?)''',
            (order_no, "running", len(days), 0, now, note),
        )
        order_id = int(cur.lastrowid)
    else:
        order_no = conn.execute(
            "SELECT order_no FROM close_orders WHERE id=?", (order_id,)
        ).fetchone()["order_no"]
    cur = conn.execute(
        "INSERT INTO close_order_attempts(order_id, attempt_no, status, started_at) VALUES (?,?,?,?)",
        (order_id, attempt_no, "running", now),
    )
    attempt_id = int(cur.lastrowid)
    emit_audit(
        conn, "order_attempt", order_id=order_id, attempt_id=attempt_id,
        detail=f"第 {attempt_no} 次提交，勾选 {len(days)} 日：{', '.join(days)}",
    )

    results = [_attempt_day(conn, order_id, attempt_id, d, now) for d in days]
    rejected = [r for r in results if r["status"] == "rejected"]

    if not rejected:
        finished = _now()
        conn.execute(
            "UPDATE close_orders SET status='committed', sealed_count=?, finished_at=? WHERE id=?",
            (len(days), finished, order_id),
        )
        conn.execute(
            "UPDATE close_order_attempts SET status='committed', finished_at=? WHERE id=?",
            (finished, attempt_id),
        )
        emit_audit(
            conn, "order_committed", order_id=order_id, attempt_id=attempt_id,
            detail=f"整单结案 {len(days)} 日",
        )
        conn.commit()
        return _receipt(order_id, order_no, attempt_id, attempt_no, "committed", results, None)

    # 原子回滚：所有日的租约、封印、批态、逐日结果随事务一并撤销，
    # 各日恢复提交前写闸（无 sealed 批 → 装载入口重新开放）。
    conn.rollback()
    error = f"{len(rejected)} 个到货日未通过结案校验，整单已回滚：" + "；".join(
        f"{r['dock_day']}（{r['reason']}）" for r in rejected
    )
    return _record_failure(conn, persisted_order_id, attempt_no, days, results, error, note)


def submit_order(days: list[str], note: str | None = None) -> dict:
    norm_days = _normalize_days(days)
    conn = connect()
    try:
        return _run_attempt(conn, None, 1, norm_days, note)
    finally:
        conn.close()


def retry_order(order_id: int) -> dict:
    conn = connect()
    try:
        order = conn.execute("SELECT * FROM close_orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise LookupError("结案工单不存在")
        if order["status"] == "committed":
            raise ValueError("工单已整单结案，不能重试")
        days = [
            r["dock_day"]
            for r in conn.execute(
                "SELECT DISTINCT dock_day FROM close_order_days WHERE order_id=? ORDER BY dock_day",
                (order_id,),
            ).fetchall()
        ]
        if not days:
            raise ValueError("工单没有可重试的到货日")
        attempt_no = conn.execute(
            "SELECT COALESCE(MAX(attempt_no), 0) m FROM close_order_attempts WHERE order_id=?",
            (order_id,),
        ).fetchone()["m"] + 1
        return _run_attempt(conn, order_id, attempt_no, days, order["note"])
    finally:
        conn.close()


def list_candidates() -> dict:
    """结案工单勾选页数据：有 open 批次的可结案日 + 已结案日。"""
    conn = connect()
    try:
        open_rows = conn.execute(
            '''SELECT b.dock_day, b.id AS batch_id, b.batch_no, b.matched_count, b.variance_count,
                      (SELECT COUNT(*) FROM variance_rows v WHERE v.batch_id=b.id AND v.fate='pending') AS pending_count
               FROM reconcile_batches b
               WHERE b.status='open'
               ORDER BY b.dock_day''',
        ).fetchall()
        sealed_rows = conn.execute(
            '''SELECT b.dock_day, b.batch_no, b.sealed_at,
                      (SELECT d.order_id FROM close_order_days d
                       WHERE d.batch_id=b.id AND d.status='sealed' ORDER BY d.id DESC LIMIT 1) AS order_id
               FROM reconcile_batches b
               WHERE b.status='sealed'
               ORDER BY b.dock_day DESC''',
        ).fetchall()
        return {
            "open": [dict(r) for r in open_rows],
            "sealed": [dict(r) for r in sealed_rows],
        }
    finally:
        conn.close()


def list_orders() -> list[dict]:
    conn = connect()
    try:
        orders = conn.execute("SELECT * FROM close_orders ORDER BY id DESC LIMIT 100").fetchall()
        out = []
        for o in orders:
            att = conn.execute(
                "SELECT * FROM close_order_attempts WHERE order_id=? ORDER BY attempt_no DESC LIMIT 1",
                (o["id"],),
            ).fetchone()
            days = []
            if att:
                days = conn.execute(
                    "SELECT * FROM close_order_days WHERE attempt_id=? ORDER BY dock_day",
                    (att["id"],),
                ).fetchall()
            out.append({
                **dict(o),
                "latest_attempt": dict(att) if att else None,
                "days": [dict(d) for d in days],
            })
        return out
    finally:
        conn.close()


def get_order(order_id: int) -> dict | None:
    conn = connect()
    try:
        o = conn.execute("SELECT * FROM close_orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        attempts = conn.execute(
            "SELECT * FROM close_order_attempts WHERE order_id=? ORDER BY attempt_no",
            (order_id,),
        ).fetchall()
        att_out = []
        for a in attempts:
            days = conn.execute(
                "SELECT * FROM close_order_days WHERE attempt_id=? ORDER BY dock_day",
                (a["id"],),
            ).fetchall()
            att_out.append({**dict(a), "days": [dict(d) for d in days]})
        leases = conn.execute(
            "SELECT * FROM day_leases WHERE order_id=? ORDER BY id",
            (order_id,),
        ).fetchall()
        return {**dict(o), "attempts": att_out, "leases": [dict(r) for r in leases]}
    finally:
        conn.close()


def list_failures() -> list[dict]:
    """失败清单页数据：失败工单 + 最新失败 attempt 的失败日清单与被回滚日。"""
    conn = connect()
    try:
        orders = conn.execute(
            "SELECT * FROM close_orders WHERE status='failed' ORDER BY id DESC LIMIT 100"
        ).fetchall()
        out = []
        for o in orders:
            att = conn.execute(
                "SELECT * FROM close_order_attempts WHERE order_id=? AND status='failed' ORDER BY attempt_no DESC LIMIT 1",
                (o["id"],),
            ).fetchone()
            if not att:
                continue
            days = conn.execute(
                "SELECT * FROM close_order_days WHERE attempt_id=? ORDER BY dock_day",
                (att["id"],),
            ).fetchall()
            out.append({
                "order_id": o["id"],
                "order_no": o["order_no"],
                "note": o["note"],
                "attempt_id": att["id"],
                "attempt_no": att["attempt_no"],
                "error": att["error"],
                "finished_at": att["finished_at"],
                "failed_days": [dict(d) for d in days if d["status"] == "rejected"],
                "rolled_back_days": [dict(d) for d in days if d["status"] == "rolled_back"],
            })
        return out
    finally:
        conn.close()


def list_audit(dock_day: str | None = None, order_id: int | None = None, limit: int = 200) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM audit_events"
        cond, args = [], []
        if dock_day:
            cond.append("dock_day=?")
            args.append(dock_day)
        if order_id is not None:
            cond.append("order_id=?")
            args.append(order_id)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()
