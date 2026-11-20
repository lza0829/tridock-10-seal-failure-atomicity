from __future__ import annotations
from app.db import connect

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expect_lines (
  id INTEGER PRIMARY KEY,
  doc_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receive_lines (
  id INTEGER PRIMARY KEY,
  receive_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirm_lines (
  id INTEGER PRIMARY KEY,
  confirm_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL UNIQUE,
  dock_day TEXT NOT NULL,
  matched_count INTEGER NOT NULL,
  variance_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  sealed_at TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS match_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  aligned INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL NOT NULL,
  receive_qty REAL NOT NULL,
  confirm_qty REAL NOT NULL,
  fate TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS late_spill (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none'
);
-- 结案工单：一次勾选多日提交，整单原子（任一日失败则整单回滚）。
CREATE TABLE IF NOT EXISTS close_orders (
  id INTEGER PRIMARY KEY,
  order_no TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,               -- committed | failed
  day_count INTEGER NOT NULL,
  sealed_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  note TEXT
);
-- 工单 attempt：每次提交/重试产生一条，工单状态以最新 attempt 为准。
CREATE TABLE IF NOT EXISTS close_order_attempts (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES close_orders(id),
  attempt_no INTEGER NOT NULL,
  status TEXT NOT NULL,               -- committed | failed
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  UNIQUE(order_id, attempt_no)
);
-- 逐日结果：sealed=本日已封印；rejected=本日未过校验；rolled_back=本日无辜被整单回滚。
CREATE TABLE IF NOT EXISTS close_order_days (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES close_orders(id),
  attempt_id INTEGER NOT NULL REFERENCES close_order_attempts(id),
  dock_day TEXT NOT NULL,
  batch_id INTEGER,
  status TEXT NOT NULL,
  reason TEXT,
  UNIQUE(attempt_id, dock_day)
);
-- 逐日租约：held 状态每日最多一条（部分唯一索引），防止并发工单双封同日；
-- 封印成功后租约转为 converted 留痕；整单回滚时随事务消失，不留残留锁。
CREATE TABLE IF NOT EXISTS day_leases (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  order_id INTEGER NOT NULL,
  attempt_id INTEGER NOT NULL,
  state TEXT NOT NULL,                -- held | converted
  acquired_at TEXT NOT NULL,
  released_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS day_leases_held ON day_leases(dock_day) WHERE state='held';
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  dock_day TEXT,
  order_id INTEGER,
  attempt_id INTEGER,
  detail TEXT
);
'''


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]:
            return
        conn.execute("INSERT INTO settings(key, value) VALUES ('dock_day', ?)", ("2026-11-18",))
        suppliers = [
            ("SUP-A", "东海食材"),
            ("SUP-B", "北岭包装"),
            ("SUP-C", "南湾百货"),
        ]
        for code, title in suppliers:
            conn.execute("INSERT INTO suppliers(code, title) VALUES (?,?)", (code, title))
        expects = [
            ("EX-1001", "SUP-A", "SKU-米-25", "2026-11-18", 40, "早班预报"),
            ("EX-1002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("EX-1003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("EX-1004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, None),
            ("EX-0901", "SUP-A", "SKU-米-25", "2026-11-17", 10, "昨日"),
        ]
        for row in expects:
            conn.execute(
                '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T08:00:00Z"),
            )
        receives = [
            ("RC-2001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "2026-11-18T09:10:00Z", "少2"),
            ("RC-2002", "SUP-A", "SKU-油-5", "2026-11-18", 20, "2026-11-18T09:20:00Z", None),
            ("RC-2003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, "2026-11-18T10:00:00Z", None),
            ("RC-2004", "SUP-C", "SKU-巾-卷", "2026-11-18", 66, "2026-11-18T11:00:00Z", "多6"),
        ]
        for row in receives:
            conn.execute(
                '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (*row, row[5]),
            )
        confirms = [
            ("CF-3001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "回执按实收"),
            ("CF-3002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("CF-3003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("CF-3004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, "回执仍报预报数"),
        ]
        for row in confirms:
            conn.execute(
                '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T12:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()
