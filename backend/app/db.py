from __future__ import annotations
import sqlite3
from app.config import DATA_DIR, DB_PATH


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 多请求并发写时，后到的写事务最多等待 5s，而不是立刻 SQLITE_BUSY。
    # 结案工单用 BEGIN IMMEDIATE 串行化，配合此超时保证并发工单有序结算。
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
