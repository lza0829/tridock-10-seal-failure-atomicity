#!/usr/bin/env python3
"""结案工单·工单级原子架构 验收脚本。

自包含：用临时数据目录起一套独立 API（端口 10999），跑完全部场景后关闭。

用法：
    python3 scripts/verify_atomicity.py            # 自动寻找 backend/.venv 或当前 python
    API_BASE=http://127.0.0.1:10900/api python3 scripts/verify_atomicity.py   # 打已有服务

核对口径（原子架构）：
  S1 两日中一日缺对账证据 → 整单回滚，两日都不封，回执给出失败日清单，总览不增加
  S2 原子失败后各日仍可重新装载（写闸恢复开放）
  S3 修复证据后重试整单成功；成功日再写三腿被写闸拒绝；总览计数同步
  S4 失败清单可查（失败日 + 被牵连回滚日）；重试成功后移出清单
  S5 并发两工单不得双封同日（恰一单 committed，同日仅一个 sealed 批）
  S6 全局不变量：不存在已关闸却无对应 sealed 批的到货日
  S7 未决差额日工单被拒，失败日不留半截批头（批次仍 open）
  S8 对账后改写三腿 → 证据过期被拒；重跑对账刷新后重试成功
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("API_BASE", "http://127.0.0.1:10999/api")
PASS, FAIL = [], []


def call(method, path, body=None, expect_ok=True):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        data = json.loads(e.read() or b"null")
        if expect_ok:
            raise
        return e.code, data


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def scenario_suite():
    # ---------- 准备：日 A=2026-11-18 证据完备（跑批+处置差额） ----------
    _, b = call("POST", "/reconcile/run", {"dock_day": "2026-11-18"})
    assert b["variance_count"] == 2, b
    _, vs = call("GET", f"/reconcile/variances?batch_id={b['batch_id']}")
    for v in vs:
        fate = "accept_short" if v["receive_qty"] < v["expect_qty"] else "accept_over"
        call("POST", f"/reconcile/variances/{v['id']}/fate", {"fate": fate})

    # 日 B=2026-11-19：三腿数据齐全且对齐，但【不跑对账】→ 缺对账证据
    for i, (s, sku, q) in enumerate([("SUP-A", "SKU-米-25", 10), ("SUP-B", "SKU-箱-大", 5)]):
        call("POST", "/expects", {"doc_no": f"EX-B{i}", "supplier_code": s, "sku": sku, "dock_day": "2026-11-19", "qty": q})
        call("POST", "/receives", {"receive_no": f"RC-B{i}", "supplier_code": s, "sku": sku, "dock_day": "2026-11-19", "qty": q})
        call("POST", "/confirms", {"confirm_no": f"CF-B{i}", "supplier_code": s, "sku": sku, "dock_day": "2026-11-19", "qty": q})

    # ---------- S1：两日中一日缺对账证据 → 原子架构两日都不封 ----------
    _, r1 = call("POST", "/close-orders", {"days": ["2026-11-18", "2026-11-19"], "note": "场景1"})
    check("S1 整单状态=failed", r1["status"] == "failed", r1.get("error"))
    d18 = next(x for x in r1["days"] if x["dock_day"] == "2026-11-18")
    d19 = next(x for x in r1["days"] if x["dock_day"] == "2026-11-19")
    check("S1 证据完备日被整单回滚(rolled_back)", d18["status"] == "rolled_back", str(d18))
    check("S1 缺证据日被拒(rejected)+原因", d19["status"] == "rejected" and "对账证据" in d19["reason"], str(d19))
    check("S1 回执给出失败日清单", [x["dock_day"] for x in r1["failed_days"]] == ["2026-11-19"])
    _, dash = call("GET", "/dashboard")
    check("S1 总览成功结案数不增加", dash["sealed_day_count"] == 0 and dash["committed_order_count"] == 0)
    check("S1 总览失败工单+1", dash["failed_order_count"] == 1)
    row18 = next(x for x in dash["days"] if x["dock_day"] == "2026-11-18")
    row19 = next(x for x in dash["days"] if x["dock_day"] == "2026-11-19")
    check("S1 两日写闸均开放（无半截sealed）", not row18["sealed"] and not row19["sealed"])

    # ---------- S2：原子失败后各日仍可重新装载 ----------
    ok = True
    try:
        call("POST", "/expects", {"doc_no": "EX-A-RE", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-18", "qty": 1})
        call("POST", "/receives", {"receive_no": "RC-B-RE", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-19", "qty": 1})
    except Exception:
        ok = False
    check("S2 原子失败后各日仍可重新装载", ok)

    # 重新装载后 A 的证据过期 → 重跑对账刷新，并处置新差额
    call("POST", "/reconcile/run", {"dock_day": "2026-11-18"})
    _, b18 = call("GET", "/reconcile/batches?dock_day=2026-11-18")
    _, vs = call("GET", f"/reconcile/variances?batch_id={b18[0]['id']}")
    for v in vs:
        if v["fate"] == "pending":
            call("POST", f"/reconcile/variances/{v['id']}/fate", {"fate": "accept_short"})
    # B 补对账证据；B 刚被重新装载一行 receive → 先补齐三腿再跑批
    call("POST", "/expects", {"doc_no": "EX-B-RE", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-19", "qty": 1})
    call("POST", "/confirms", {"confirm_no": "CF-B-RE", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-19", "qty": 1})
    call("POST", "/reconcile/run", {"dock_day": "2026-11-19"})

    # ---------- S4（前置）：失败清单可查 ----------
    _, fs = call("GET", "/close-orders/failures")
    f1 = next((x for x in fs if x["order_id"] == r1["order_id"]), None)
    check("S4 失败清单可查(含失败日+被回滚日)",
          f1 and [d["dock_day"] for d in f1["failed_days"]] == ["2026-11-19"]
          and [d["dock_day"] for d in f1["rolled_back_days"]] == ["2026-11-18"],
          json.dumps(f1, ensure_ascii=False)[:200] if f1 else "missing")

    # ---------- S3：修复后重试 → 整单成功；成功日再写三腿失败 ----------
    _, r2 = call("POST", f"/close-orders/{r1['order_id']}/retry")
    check("S3 重试后整单 committed", r2["status"] == "committed" and r2["sealed_count"] == 2, r2.get("error") or "")
    check("S3 两日均 sealed", all(x["status"] == "sealed" for x in r2["days"]))
    code, _ = call("POST", "/expects", {"doc_no": "EX-X1", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-18", "qty": 1}, expect_ok=False)
    check("S3 成功日再写预报被写闸拒绝", code == 400)
    code, _ = call("POST", "/receives", {"receive_no": "RC-X1", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-19", "qty": 1}, expect_ok=False)
    check("S3 成功日再写实收被写闸拒绝", code == 400)
    code, _ = call("POST", "/confirms", {"confirm_no": "CF-X1", "supplier_code": "SUP-A", "sku": "SKU-米-25", "dock_day": "2026-11-19", "qty": 1}, expect_ok=False)
    check("S3 成功日再写回执被写闸拒绝", code == 400)
    _, dash = call("GET", "/dashboard")
    check("S3 总览结案计数=2日/1单", dash["sealed_day_count"] == 2 and dash["committed_order_count"] == 1)
    _, fs = call("GET", "/close-orders/failures")
    check("S4 重试成功后移出失败清单", all(x["order_id"] != r1["order_id"] for x in fs))

    # ---------- S5：并发两工单不得双封同日 ----------
    call("POST", "/expects", {"doc_no": "EX-C0", "supplier_code": "SUP-C", "sku": "SKU-巾-卷", "dock_day": "2026-11-20", "qty": 7})
    call("POST", "/receives", {"receive_no": "RC-C0", "supplier_code": "SUP-C", "sku": "SKU-巾-卷", "dock_day": "2026-11-20", "qty": 7})
    call("POST", "/confirms", {"confirm_no": "CF-C0", "supplier_code": "SUP-C", "sku": "SKU-巾-卷", "dock_day": "2026-11-20", "qty": 7})
    call("POST", "/reconcile/run", {"dock_day": "2026-11-20"})

    results = [None, None]

    def submit(idx):
        try:
            _, r = call("POST", "/close-orders", {"days": ["2026-11-20"]})
            results[idx] = r
        except Exception as e:  # noqa: BLE001
            results[idx] = {"status": "exception", "error": str(e)}

    t1 = threading.Thread(target=submit, args=(0,))
    t2 = threading.Thread(target=submit, args=(1,))
    t1.start(); t2.start(); t1.join(); t2.join()
    statuses = sorted(r["status"] for r in results)
    check("S5 并发两工单：恰一单 committed", statuses == ["committed", "failed"], str(statuses))
    _, bs = call("GET", "/reconcile/batches?dock_day=2026-11-20")
    sealed_bs = [b for b in bs if b["status"] == "sealed"]
    check("S5 同日仅一个 sealed 批（无双封）", len(sealed_bs) == 1, f"sealed={len(sealed_bs)}")

    # ---------- S7：差额未处置日工单被拒，且不留半截批 ----------
    call("POST", "/expects", {"doc_no": "EX-D0", "supplier_code": "SUP-B", "sku": "SKU-箱-大", "dock_day": "2026-11-21", "qty": 10})
    call("POST", "/receives", {"receive_no": "RC-D0", "supplier_code": "SUP-B", "sku": "SKU-箱-大", "dock_day": "2026-11-21", "qty": 8})
    call("POST", "/confirms", {"confirm_no": "CF-D0", "supplier_code": "SUP-B", "sku": "SKU-箱-大", "dock_day": "2026-11-21", "qty": 10})
    call("POST", "/reconcile/run", {"dock_day": "2026-11-21"})
    _, r7 = call("POST", "/close-orders", {"days": ["2026-11-21"]})
    check("S7 未决差额日工单回滚", r7["status"] == "failed" and "差额" in r7["days"][0]["reason"], str(r7["days"][0]))
    _, bs = call("GET", "/reconcile/batches?dock_day=2026-11-21")
    check("S7 失败日无半截批头（批次仍 open 未封）", all(b["status"] == "open" for b in bs))
    _, dash = call("GET", "/dashboard")
    check("S7 失败日写闸仍开放", not next(x for x in dash["days"] if x["dock_day"] == "2026-11-21")["sealed"])

    # ---------- S8：证据过期（对账后改写三腿）→ 拒；重跑刷新后可封 ----------
    for leg, no in [("expects", "EX-E0"), ("receives", "RC-E0"), ("confirms", "CF-E0")]:
        key = {"expects": "doc_no", "receives": "receive_no", "confirms": "confirm_no"}[leg]
        call("POST", f"/{leg}", {key: no, "supplier_code": "SUP-A", "sku": "SKU-油-5", "dock_day": "2026-11-22", "qty": 3})
    call("POST", "/reconcile/run", {"dock_day": "2026-11-22"})
    call("POST", "/expects", {"doc_no": "EX-E1", "supplier_code": "SUP-A", "sku": "SKU-油-5", "dock_day": "2026-11-22", "qty": 1})
    _, r8 = call("POST", "/close-orders", {"days": ["2026-11-22"]})
    check("S8 对账后改写三腿 → 证据过期被拒", r8["status"] == "failed" and "过期" in r8["days"][0]["reason"], str(r8["days"][0]))
    call("POST", "/receives", {"receive_no": "RC-E1", "supplier_code": "SUP-A", "sku": "SKU-油-5", "dock_day": "2026-11-22", "qty": 1})
    call("POST", "/confirms", {"confirm_no": "CF-E1", "supplier_code": "SUP-A", "sku": "SKU-油-5", "dock_day": "2026-11-22", "qty": 1})
    call("POST", "/reconcile/run", {"dock_day": "2026-11-22"})
    _, r8b = call("POST", f"/close-orders/{r8['order_id']}/retry")
    check("S8 重跑对账刷新证据后重试成功", r8b["status"] == "committed", r8b.get("error") or "")

    # ---------- S6：全局不变量——不存在已关闸却无 sealed 批的日 ----------
    _, dash = call("GET", "/dashboard")
    inv = all((x["sealed"] == (x["batch_status"] == "sealed")) for x in dash["days"])
    check("S6 无部分日关闸却无对应 sealed 批", inv)
    check("S6 总览计数与实际 sealed 日一致",
          dash["sealed_day_count"] == sum(1 for x in dash["days"] if x["sealed"]))

    # ---------- 审计联动 ----------
    _, audit = call("GET", f"/audit?order_id={r1['order_id']}")
    acts = {a["action"] for a in audit}
    check("审计：工单 attempt/回滚/提交均留痕", {"order_attempt", "order_failed", "order_committed"} <= acts, str(acts))


def main():
    if "API_BASE" in os.environ:
        scenario_suite()
    else:
        py = REPO / "backend" / ".venv" / "bin" / "python"
        python = str(py) if py.exists() else sys.executable
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "TRIDOCK_DATA": tmp}
            proc = subprocess.Popen(
                [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "10999"],
                cwd=REPO / "backend", env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(50):
                    try:
                        urllib.request.urlopen(BASE + "/dashboard", timeout=1)
                        break
                    except Exception:  # noqa: BLE001
                        time.sleep(0.2)
                else:
                    print("API 未能启动")
                    return 1
                scenario_suite()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
    print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
