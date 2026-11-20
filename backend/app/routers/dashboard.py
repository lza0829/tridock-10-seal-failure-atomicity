from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.gate import is_sealed

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day = conn.execute("SELECT value FROM settings WHERE key='dock_day'").fetchone()["value"]
        expect_n = conn.execute("SELECT COUNT(*) c FROM expect_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        receive_n = conn.execute("SELECT COUNT(*) c FROM receive_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        confirm_n = conn.execute("SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        batch = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        pending = 0
        if batch:
            pending = conn.execute(
                "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND fate='pending'",
                (batch["id"],),
            ).fetchone()["c"]

        # 总览结案计数：与写闸同源（sealed 批次），工单回滚则此处必然不增加。
        sealed_day_count = conn.execute(
            "SELECT COUNT(DISTINCT dock_day) c FROM reconcile_batches WHERE status='sealed'"
        ).fetchone()["c"]
        committed_orders = conn.execute(
            "SELECT COUNT(*) c FROM close_orders WHERE status='committed'"
        ).fetchone()["c"]
        failed_orders = conn.execute(
            "SELECT COUNT(*) c FROM close_orders WHERE status='failed'"
        ).fetchone()["c"]

        day_rows = []
        days = conn.execute(
            '''SELECT dock_day FROM expect_lines
               UNION SELECT dock_day FROM receive_lines
               UNION SELECT dock_day FROM confirm_lines
               UNION SELECT dock_day FROM reconcile_batches
               ORDER BY dock_day DESC LIMIT 30'''
        ).fetchall()
        for r in days:
            d = r["dock_day"]
            b = conn.execute(
                "SELECT status, batch_no FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
                (d,),
            ).fetchone()
            day_rows.append({
                "dock_day": d,
                "sealed": is_sealed(conn, d),
                "expect_count": conn.execute("SELECT COUNT(*) c FROM expect_lines WHERE dock_day=?", (d,)).fetchone()["c"],
                "receive_count": conn.execute("SELECT COUNT(*) c FROM receive_lines WHERE dock_day=?", (d,)).fetchone()["c"],
                "confirm_count": conn.execute("SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (d,)).fetchone()["c"],
                "batch_status": b["status"] if b else None,
                "batch_no": b["batch_no"] if b else None,
            })

        return {
            "dock_day": day,
            "sealed": is_sealed(conn, day),
            "expect_count": expect_n,
            "receive_count": receive_n,
            "confirm_count": confirm_n,
            "latest_batch": dict(batch) if batch else None,
            "pending_variance": pending,
            "sealed_day_count": sealed_day_count,
            "committed_order_count": committed_orders,
            "failed_order_count": failed_orders,
            "days": day_rows,
        }
    finally:
        conn.close()
