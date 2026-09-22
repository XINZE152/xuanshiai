"""bootstrap 补列映射的回归测试（第三批安全收尾）。

背景：``DatabaseManager._ensure_required_columns`` 的 ``required_columns``
曾在 421ff28（2026-09-11）以重复的 ``"user_auth": {`` 键把实名认证补列
map（realname_status 等 23 列）静默覆盖——Python 字典字面量重复键保留最后
一个，且无任何告警。后果：2026-09-11 之后新建的库全部缺失实名认证字段，
依赖这些字段的可见性门禁（candidate_visibility）与集成测试在建表语句
（CREATE TABLE IF NOT EXISTS 不补列）之外无任何修复路径。

本测试解析该函数源码，锁死"不允许重复键"这一约束；同时抽查关键列
（realname_status / submit_ip / face_method）仍在映射中。
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _required_columns_literal() -> ast.Dict:
    source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_ensure_required_columns":
            continue
        for stmt in ast.walk(node):
            if (
                isinstance(stmt, ast.Assign)
                and isinstance(stmt.value, ast.Dict)
            ):
                targets = (
                    stmt.targets[0].id if isinstance(stmt.targets[0], ast.Name) else ""
                )
                if targets == "required_columns":
                    return stmt.value
    raise AssertionError("_ensure_required_columns 的 required_columns 字典未找到")


def test_required_columns_has_no_duplicate_table_keys() -> None:
    literal = _required_columns_literal()
    keys = [
        key.value for key in literal.keys if isinstance(key, ast.Constant)
    ]
    duplicates = {k: v for k, v in Counter(keys).items() if v > 1}
    assert duplicates == {}, (
        f"required_columns 存在重复表键（后者静默覆盖前者的补列）：{duplicates}"
    )


def test_realname_and_moderation_columns_survive() -> None:
    literal = _required_columns_literal()
    keys = {
        key.value: entry for key, entry in zip(literal.keys, literal.values)
        if isinstance(key, ast.Constant)
    }
    user_auth_cols = {
        col.value for col in keys["user_auth"].keys if isinstance(col, ast.Constant)
    }
    # 实名认证（candidate_visibility / 集成测试依赖）
    assert {"realname_status", "realname_provider", "revoked_at", "id_card_hash"} <= user_auth_cols
    # 会员认证审核（421ff28 意图补齐的字段）
    assert {"face_method", "face_vendor", "face_score", "id_card_issued"} <= user_auth_cols
    user_report_cols = {
        col.value for col in keys["user_report"].keys if isinstance(col, ast.Constant)
    }
    assert {"target_type", "action", "submit_ip"} <= user_report_cols
