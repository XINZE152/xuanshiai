"""ensure_defaults 并发安全回归（死锁 1213 修复）。

修复要点（每条对应一个静态断言）：
1. 改用 `INSERT ... ON DUPLICATE KEY UPDATE` 替代 `INSERT IGNORE`（死锁概率更低、行为更确定）。
2. 用 MySQL `GET_LOCK('ensure_admin_config_defaults', 5)` 串行化并发调用（5s 超时）。
3. 捕获 `pymysql.err.OperationalError (1213)` 自动重试最多 3 次，指数退避。
4. SELECT 一次全部已存在 namespace，不再 50+ 次单条 SELECT。
5. release lock 放在 finally 块，避免锁泄漏。

配套改动：finance.orders/withdrawals/ledger 等 list 接口的日期 query 去掉严格 pattern，
允许前端传空字符串 `?start_time=` 而不触发 422。
"""

import inspect

from app.services.admin_config import ensure_defaults


def test_ensure_defaults_uses_ondup_key_update_not_insert_ignore() -> None:
    """INSERT IGNORE 被替换为 INSERT ... ON DUPLICATE KEY UPDATE。"""
    src = inspect.getsource(ensure_defaults)
    # 提取所有 text("...")/text("""...""") SQL 字符串字面量（排除 docstring/comment 里的提及）
    import re
    sqls = re.findall(r'text\(\s*"""((?:[^"]|"(?!""))*?)"""\s*\)|text\(\s*"((?:[^"\\]|\\.)*)"\s*\)', src, flags=re.S)
    sql_blob = "\n".join((a or b) for a, b in sqls)
    assert "INSERT IGNORE" not in sql_blob, "INSERT IGNORE 仍出现在 SQL 字面量 — MySQL 1213 死锁风险未消除"
    assert "ON DUPLICATE KEY UPDATE" in sql_blob, "缺少 ON DUPLICATE KEY UPDATE 兜底"


def test_ensure_defaults_uses_get_lock_for_serialization() -> None:
    """MySQL GET_LOCK 串行化 ensure_defaults，避免并发 INSERT 触发死锁。"""
    src = inspect.getsource(ensure_defaults)
    assert "GET_LOCK" in src, "缺少 GET_LOCK 串行化"
    assert "ensure_admin_config_defaults" in src, "缺少命名锁 key"
    assert "RELEASE_LOCK" in src, "缺少 RELEASE_LOCK 释放"


def test_ensure_defaults_retries_on_deadlock_1213() -> None:
    """捕获 pymysql 1213 死锁自动重试。"""
    src = inspect.getsource(ensure_defaults)
    assert "1213" in src, "缺少 1213 死锁异常捕获"
    assert "retry" in src.lower() or "attempt" in src, "缺少重试循环"


def test_ensure_defaults_releases_lock_in_finally() -> None:
    """锁释放必须放在 finally 块，确保异常路径也释放。"""
    src = inspect.getsource(ensure_defaults)
    # finally 块存在 + RELEASE_LOCK
    assert "finally" in src, "缺少 finally 块"
    # 锁获取的状态变量必须被 finally 检查
    assert "lock_acquired" in src, "缺少 lock_acquired 状态变量"


def test_ensure_defaults_bulk_select_existing() -> None:
    """SELECT 已存在 namespace 应一次性查询（不再 50+ 次单条 SELECT）。"""
    src = inspect.getsource(ensure_defaults)
    # 一次性查询（无 namespace 参数的 SELECT）
    assert "SELECT namespace, config_json FROM admin_config_snapshot" in src, \
        "未一次性 SELECT 全部 namespace"


def test_finance_orders_date_query_tolerates_empty_string() -> None:
    """finance list 接口的日期 query 去掉严格 pattern，兼容前端 `?start_time=` 空字符串。"""
    from app.api.routes import finance as finance_routes
    src = inspect.getsource(finance_routes)
    # 不再有 `pattern=r"^\d{4}-\d{2}-\d{2}$"`
    assert 'pattern=r"^\\d{4}-\\d{2}-\\d{2}$"' not in src, \
        "finance routes 仍使用严格日期 pattern，前端空字符串会触发 422"
    # 改为宽松 max_length
    assert "max_length=10" in src, "finance routes 应当改用 max_length=10 替代 pattern"


def test_ensure_defaults_idempotent_insert_via_ondup() -> None:
    """ON DUPLICATE KEY UPDATE 占位（namespace=VALUES(namespace)）保证幂等。"""
    src = inspect.getsource(ensure_defaults)
    assert "namespace = VALUES(namespace)" in src, \
        "ON DUPLICATE KEY UPDATE 必须用占位更新保证幂等"


def test_ensure_defaults_commits_only_when_there_are_writes() -> None:
    """确保无写入时不 commit（避免无谓事务）。"""
    src = inspect.getsource(ensure_defaults)
    assert "if inserts or updates" in src or "if (inserts or updates)" in src, \
        "ensure_defaults 应当在有写入时才 commit"