# Tridock（到货三联台）

骨架：到货预报装载 → 实收入账 → 供应商回执装载 → 三方键对齐与差额处置 → 到货日结案封印。

- 前端 http://127.0.0.1:5900
- API http://127.0.0.1:10900

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10900 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5900
```

## 结案工单（一次勾选多日提交）

架构口径：**工单级原子提交**（全局唯一，不混用逐日提交）。

- 一次工单 attempt = 一个数据库事务：逐日租约 → 结案证据校验 → 批次 `open→sealed`
  封印 → 写闸生效 → 审计，全部同事务落库。
- 任一日未过校验 → 整单 ROLLBACK：所有日恢复提交前写闸与批态，不留半截 sealed；
  随后独立事务落「失败工单 + 失败日清单」回执（失败清单页可查）。
- 结案证据链：存在 open 对账批次 → 差额全部处置 → 批次快照与当前三腿一致
  （对账后改写三腿则证据过期，需重跑对账刷新）。
- 逐日租约：`day_leases` 上 `state='held'` 部分唯一索引 + `BEGIN IMMEDIATE` 串行化，
  并发两工单不得双封同日；封印成功租约转 `converted` 留痕，回滚则随事务消失。
- 总览结案计数与写闸同源（sealed 批次派生）：工单回滚则计数必然不增加，
  不存在「已关闸却无对应 sealed 批」的日。
- 前端「06 结案工单」一次勾选多日、单个请求提交 `POST /api/close-orders`，
  禁止前端循环调单日接口冒充原子。

主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/close-orders` | 提交多日结案工单（整单原子），返回逐日回执 |
| POST | `/api/close-orders/{id}/retry` | 失败工单按原日清单重试（新 attempt） |
| GET | `/api/close-orders` / `/{id}` | 工单列表 / 详情（attempt、逐日、租约） |
| GET | `/api/close-orders/candidates` | 可结案日（open 批次 + 未决差额数）与已结案日 |
| GET | `/api/close-orders/failures` | 失败清单：失败日原因 + 被牵连回滚日 |
| GET | `/api/audit` | 审计事件（可按到货日 / 工单过滤） |

## 验收

```powershell
py scripts\verify_atomicity.py   # 自包含：临时库起服务，跑 26 项原子架构场景
```
