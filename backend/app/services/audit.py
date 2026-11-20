from __future__ import annotations
import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit_audit(
    conn: sqlite3.Connection,
    action: str,
    *,
    actor: str = "operator",
    dock_day: str | None = None,
    order_id: int | None = None,
    attempt_id: int | None = None,
    detail: str | None = None,
) -> None:
    """在调用方的事务内写一条审计事件（不自行 commit，随业务事务同生共死）。"""
    conn.execute(
        '''INSERT INTO audit_events(ts, actor, action, dock_day, order_id, attempt_id, detail)
           VALUES (?,?,?,?,?,?,?)''',
        (_now(), actor, action, dock_day, order_id, attempt_id, detail),
    )
