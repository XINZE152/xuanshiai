"""客源线索联系方式唯一性守卫回归。

背景：`find_conflicting_lead_id` 曾把「排除自身」谓词 `id != :conflict_exclude_id`
并进联系方式 OR 组，生成的 SQL 形如：

    WHERE status NOT IN ('LOST','CLOSED')
      AND (phone = :p OR wechat = :w OR id != :exclude)
      ORDER BY id LIMIT 1

只要库里存在两条以上有效线索，`id != :exclude` 对任意其它行恒真，整个 OR 组恒真，
于是**任何**线索编辑（哪怕只改审核状态、完全不碰联系方式）都被误判为重复并返回 409。

本文件冻结修复后的形状：自身排除必须与联系方式组做 AND 连接。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.customer_lead_contact import find_conflicting_lead_id


class _ProbeResult:
    """软删除列探测（information_schema）的假结果，仅需 .all()。"""

    def __init__(self, rows=None) -> None:
        self._rows = list(rows or [])

    def all(self):
        return list(self._rows)


class RecordingDB:
    """只记录 SQL 的假会话，返回预设冲突 id。"""

    def __init__(self, scalar: int | None = None) -> None:
        self.sql = ""
        self.params: dict[str, Any] = {}
        self.probe_sql = ""
        self._scalar = scalar

    async def execute(self, statement, params=None):
        # 服务会先探测 customer_lead.deleted_at 列是否存在（软删除迁移）。
        # 这里返回空行 = 视为未迁移 → 跳过 deleted_at 谓词，使断言仍针对主查询 SQL 形状。
        self.probe_sql = str(statement)
        return _ProbeResult([])

    async def scalar(self, statement, params=None):
        self.sql = str(statement)
        self.params = dict(params or {})
        return self._scalar


@pytest.mark.asyncio
async def test_self_exclusion_is_anded_outside_contact_group() -> None:
    """排除自身必须出现在 OR 组之外，用 AND 连接。"""
    db = RecordingDB(scalar=None)
    await find_conflicting_lead_id(db, "13800000001", "wx_01", exclude_lead_id=7)

    assert "id != :conflict_exclude_id" in db.sql
    # OR 组必须收在括号内，且排除谓词在括号之后 —— 即 AND 连接而非并进 OR 组。
    group_end = db.sql.rindex(")")
    assert db.sql.rindex("id != :conflict_exclude_id") > group_end, (
        f"自身排除仍在 OR 组内，任何编辑都会误判冲突：{db.sql}"
    )
    assert db.params["conflict_exclude_id"] == 7


@pytest.mark.asyncio
async def test_contact_group_still_or_joined() -> None:
    """手机号与微信号之间仍是 OR —— 任一命中即算冲突。"""
    db = RecordingDB(scalar=None)
    await find_conflicting_lead_id(db, "13800000001", "wx_01", exclude_lead_id=7)

    assert "phone = :conflict_phone" in db.sql
    assert "wechat = :conflict_wechat" in db.sql
    assert " OR ".join(["phone = :conflict_phone", "wechat = :conflict_wechat"]) in db.sql


@pytest.mark.asyncio
async def test_no_exclude_clause_when_creating() -> None:
    """新建线索（不排除自身）不得出现排除谓词。"""
    db = RecordingDB(scalar=None)
    await find_conflicting_lead_id(db, "13800000001", None)

    assert "id !=" not in db.sql
    assert "conflict_exclude_id" not in db.params


@pytest.mark.asyncio
async def test_inactive_statuses_excluded() -> None:
    """弃海/已关闭线索不占用联系方式。"""
    db = RecordingDB(scalar=None)
    await find_conflicting_lead_id(db, "13800000001", None)

    assert "status NOT IN ('LOST', 'CLOSED')" in db.sql


@pytest.mark.asyncio
async def test_blank_contacts_skip_query() -> None:
    """联系方式均为空时直接返回 None，不查库。"""
    db = RecordingDB()
    assert await find_conflicting_lead_id(db, None, None) is None
    assert await find_conflicting_lead_id(db, "", "   ") is None
    assert db.sql == ""


@pytest.mark.asyncio
async def test_conflicting_id_is_returned() -> None:
    """命中冲突时返回占用方 id。"""
    db = RecordingDB(scalar=42)
    assert await find_conflicting_lead_id(db, "13800000001", None, exclude_lead_id=7) == 42
