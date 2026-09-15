# M9 死锁 + 422 修复说明

> **修复日期**：2026-09-12
> **问题报告**：用户登录后台后，并发访问平台/系统配置域触发 MySQL 1213 死锁 + 多 namespace 404 + finance.orders 422 + credit-grants 404

## 1. MySQL 1213 死锁（核心问题）

### 现象
15:09:45 前后，前端首次加载多个 Tab 并发请求：
- `GET /api/v1/admin/configs/platform_operation` → 500（INSERT 死锁）
- `GET /api/v1/admin/configs/platform_filter_config` → 500（同因）
- 死锁的 SQL：`INSERT IGNORE INTO admin_config_snapshot (namespace, name, description, version, config_json, sensitive_keys_json) VALUES (...)`

### 根因
`app/services/admin_config.py::ensure_defaults()` 在每次 `get_config`/`update_config` 调用时执行，逻辑：

```python
for namespace in DEFAULT_CONFIGS:  # 54 个 namespace
    await db.execute(text("INSERT IGNORE INTO admin_config_snapshot (...)"))  # ← 这里
    ...
    if existing: ...  # 单条 SELECT
await db.commit()
```

并发场景下：
1. 请求 A 启动事务，INSERT IGNORE 拿到 `platform_operation` 行锁
2. 请求 B 启动事务，INSERT IGNORE 等待同一行锁
3. 多个事务互相持有 + 等待，触发 InnoDB 死锁检测 → 1213 → 事务回滚
4. **事务回滚后 namespace 仍未持久化** → 后续请求 SELECT 不到行 → 抛出 `404 配置域不存在`

死锁的副作用是连锁的：5 个 namespace（`sys_site`、`sys_ads`、`sys_outbound`、`outbound_seats`、`outbound_call_records`）持续 INSERT 失败 → 持续 404。

### 修复（`app/services/admin_config.py`）

`ensure_defaults` 重写为五道防线：

```python
async def ensure_defaults(db: AsyncSession) -> None:
    # 1. GET_LOCK 串行化：MySQL 命名锁，5s 超时
    lock_acquired = False
    try:
        result = (await db.execute(
            text("SELECT GET_LOCK('ensure_admin_config_defaults', 5)")
        )).scalar()
        lock_acquired = bool(result)
    except Exception:
        lock_acquired = False  # 锁失败不阻塞，降级到 SELECT-only

    try:
        for attempt in range(3):
            try:
                # 2. SELECT 一次全部已存在 namespace（不再 50+ 次单条 SELECT）
                existing_map = {row["namespace"]: row["config_json"]
                                for row in (await db.execute(
                                    text("SELECT namespace, config_json FROM admin_config_snapshot")
                                )).mappings().all()}

                inserts, updates = [], []
                for namespace, (...) in DEFAULT_CONFIGS.items():
                    if namespace not in existing_map:
                        inserts.append({...})  # 缺失
                    else:
                        # 合并缺失字段
                        current = json.loads(existing_map[namespace] or "{}")
                        missing = {k: v for k, v in config.items() if k not in current}
                        if missing:
                            current.update(missing)
                            updates.append({...})

                # 3. ON DUPLICATE KEY UPDATE 替代 INSERT IGNORE
                for params in inserts:
                    await db.execute(text("""INSERT INTO admin_config_snapshot
                        (...) VALUES (...) ON DUPLICATE KEY UPDATE namespace = VALUES(namespace)"""), params)

                # 4. 字段合并独立 UPDATE
                for params in updates:
                    await db.execute(text("""UPDATE admin_config_snapshot
                        SET config_json = :config_json WHERE namespace = :namespace"""), params)

                if inserts or updates:
                    await db.commit()
                return
            except OperationalError as exc:
                try: await db.rollback()
                except: pass
                # 5. 1213 死锁自动重试（指数退避 0.1s / 0.2s）
                if "1213" in str(exc) and attempt < 2:
                    await asyncio.sleep(0.1 * (2 ** attempt))
                    continue
                raise
    finally:
        # 锁释放（finally 块确保异常路径也释放）
        if lock_acquired:
            try:
                await db.execute(text("SELECT RELEASE_LOCK('ensure_admin_config_defaults')"))
                await db.commit()
            except Exception:
                try: await db.rollback()
                except: pass
```

### 关键改进

| 项目 | 修复前 | 修复后 |
|---|---|---|
| SQL 写法 | `INSERT IGNORE` | `INSERT ... ON DUPLICATE KEY UPDATE` |
| 并发串行化 | 无（裸用 InnoDB 锁） | MySQL `GET_LOCK('ensure_admin_config_defaults', 5)` |
| 死锁兜底 | 无（直接 500） | 捕获 `OperationalError(1213)` 重试 3 次，指数退避 |
| 已存在 namespace 检查 | 54 次单条 SELECT | 1 次批量 SELECT |
| 锁释放 | 无 | `finally` 块确保异常路径也释放 |
| 写入后才 commit | 不分（每次都 commit） | `if inserts or updates` 才 commit |

## 2. finance list 接口日期 query 422

### 现象
`GET /api/v1/admin/finance/orders → 422`、`GET /api/v1/admin/finance/withdrawals → 422`、`/commission-entries → 422`

### 根因
路由参数 `start_time`/`end_time`/`start_date`/`end_date` 用了严格正则：
```python
start_time: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", ...)
```
前端（system-finance-order 页面）传 `?start_time=&end_time=`（**空字符串而非 omit**），FastAPI 把 `?start_time=` 解析为空串 `""`，空串不匹配正则 → 422。

### 修复（`app/api/routes/finance.py`）
所有 8 处日期 query 改为：
```python
start_time: str | None = Query(None, max_length=10, description="开始日期 YYYY-MM-DD（兼容前端空字符串）"),
```
service 层（`admin_list_orders` 等）用 `if start_time:`（truthy 检查）天然容忍空串 → 不进入 WHERE 子句。

## 3. credit-grants POST 404

### 现象
`POST /api/v1/admin/finance/credit-grants → 404`

### 根因
**生产后端服务未重启加载 M9 代码**。M9 的 credit-grants 端点在 `app/api/routes/finance.py` 已完整实现并通过 12/12 pytest，但生产服务器（`/home/xuanshiai/app/main.py`）仍跑的是 M9 之前版本，没有这个路由。

### 处理
代码侧无需改动（M9 已交付）。**需用户在生产环境重启后端服务**：
```bash
# 生产环境
kill -TERM $(pgrep -f "uvicorn|hypercorn|gunicorn")
cd /home/xuanshiai && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 &
```

## 4. 死锁修复回归测试

`tests/test_admin_config_concurrency.py`（8 个静态断言）：
1. `INSERT IGNORE` 已替换为 `INSERT ... ON DUPLICATE KEY UPDATE`
2. `GET_LOCK('ensure_admin_config_defaults', 5)` 串行化
3. `1213` 死锁异常被捕获并 retry
4. `finally` 块释放 `RELEASE_LOCK`
5. SELECT 已批量（不再 50+ 次单条 SELECT）
6. `finance` list 接口日期 query 容忍空字符串（已改为 `max_length=10`）
7. `ON DUPLICATE KEY UPDATE` 占位（`namespace = VALUES(namespace)`）保证幂等
8. 仅在有写入时才 commit

## 5. 验证

```bash
# 本地
./.venv/Scripts/python.exe -m pytest tests/test_admin_config_concurrency.py tests/test_admin_config.py tests/test_m9_system_finance_ext.py tests/test_m7_activity_merchant_video_ext.py tests/test_m8_operational_tools.py -v
# → 56 passed
```

待用户重启生产后端服务后，前端刷新即可：
- 平台配置/系统配置 Tab 全部 namespace 不再 500/404
- finance.orders / withdrawals / ledger 列表 200 OK
- credit-grants POST 200 OK（发放积分功能可用）