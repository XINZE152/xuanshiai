"""运行期配置消费层：管理端配置 -> 业务行为。

设计口径：
- 所有函数带 db（AsyncSession）。缺失/异常一律回退默认值，绝不因配置表
  不可用而抛错，保持与旧 get_runtime_value 相同的“fail-open 到默认”语义。
- 布尔读取对字符串/数字容错（兼容早期快照手填 "true"/"1"）。
- 管理端尚未保存过嵌套分组（例如 power-config 的 browse/view/line/other）
  时按旧行为处理；一旦保存过，嵌套值即成为业务运行的真实依据。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def load_config(db: AsyncSession, namespace: str) -> dict[str, Any]:
    """读取一个配置域的快照 JSON；任何异常都返回 {}。"""
    if not isinstance(db, AsyncSession):
        return {}
    try:
        result = await db.execute(
            text("SELECT config_json FROM admin_config_snapshot WHERE namespace = :namespace"),
            {"namespace": namespace},
        )
        row = result.mappings().first() if result is not None else None
    except Exception:
        return {}
    if not row:
        return {}
    try:
        value = json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def runtime_bool(db: AsyncSession, namespace: str, key: str, default: bool) -> bool:
    return _as_bool((await load_config(db, namespace)).get(key), default)


async def runtime_str(db: AsyncSession, namespace: str, key: str, default: str) -> str:
    value = (await load_config(db, namespace)).get(key)
    return str(value) if value not in (None, "") else default


def _bool_of(cfg: dict[str, Any], key: str, default: bool) -> bool:
    return _as_bool(cfg.get(key), default)


# --------------------------------------------------------------------------- #
# 注册 / 浏览总开关（platform_operation 与 sys_access 同时生效）
# --------------------------------------------------------------------------- #

async def platform_registration_open(db: AsyncSession) -> bool:
    """新用户注册总开关：platform_operation.registration_enabled 与
    sys_access.register_enabled 同时为开才允许新注册。"""
    operation = await runtime_bool(db, "platform_operation", "registration_enabled", True)
    access = await runtime_bool(db, "sys_access", "register_enabled", True)
    return operation and access


async def platform_browse_open(db: AsyncSession) -> bool:
    """浏览/发现总开关：platform_operation.browse_enabled 与
    sys_access.browse_enabled 同时为开才开放推荐/搜索/资料浏览。"""
    operation = await runtime_bool(db, "platform_operation", "browse_enabled", True)
    access = await runtime_bool(db, "sys_access", "browse_enabled", True)
    return operation and access


async def platform_maintenance_message(db: AsyncSession) -> str:
    return await runtime_str(db, "platform_operation", "maintenance_message", "平台正在维护中，请稍后再试。")


# --------------------------------------------------------------------------- #
# 平台权限配置（power-config） -> 行为解释层
# 前端把控件保存为 browse/view/state/line/other 五组嵌套键；本层读取嵌套键
# 并把“前端值词汇”解释为后端可执行语义。未保存嵌套分组时返回 None（沿用旧行为）。
# --------------------------------------------------------------------------- #

async def match_line_permissions(db: AsyncSession) -> dict[str, Any]:
    """线上牵线权限（power-config 卡片4）。

    - realname_allowed: line.realname == "yes" -> 未实名也可发起牵线
    - be_linked_allowed: line.beLinked == "yes" -> 允许向未实名对象发起牵线
    - apply_daily: line.times 的数值（>0 才作为统一每日上限；0/缺省回退旧限额）
    - active: 是否已保存过 line 分组（决定上述开关是否生效）
    """
    cfg = await load_config(db, "platform_permissions")
    line = cfg.get("line")
    if not isinstance(line, dict):
        return {"realname_allowed": None, "be_linked_allowed": None,
                "apply_daily": None, "active": False}
    return {
        "realname_allowed": _bool_of(line, "realname", False) if line.get("realname") is not None else None,
        "be_linked_allowed": _bool_of(line, "beLinked", False) if line.get("beLinked") is not None else None,
        "apply_daily": _as_int(line.get("times"), 0) if line.get("times") is not None else None,
        "active": True,
    }


async def same_id_multiple_accounts_allowed(db: AsyncSession) -> bool:
    """其他权限：同一身份证号是否允许多账号实名（other.multiAccount == "yes"）。"""
    cfg = await load_config(db, "platform_permissions")
    other = cfg.get("other")
    if not isinstance(other, dict) or other.get("multiAccount") is None:
        return False  # 旧行为：一律禁止一证多号
    return _bool_of(other, "multiAccount", False)


# --------------------------------------------------------------------------- #
# 财务配置（finance） -> 提现/退款行为
# --------------------------------------------------------------------------- #

async def withdrawal_policy(db: AsyncSession) -> dict[str, Any]:
    cfg = await load_config(db, "finance")
    wd = cfg.get("withdrawal")
    if not isinstance(wd, dict):
        return {"enabled": True, "min_amount": "0.00"}  # 旧行为：开放
    return {
        "enabled": _bool_of(wd, "enabled", True),
        "min_amount": str(wd.get("min_amount") or "0.00"),
    }


async def refund_manual_review(db: AsyncSession) -> bool:
    cfg = await load_config(db, "finance")
    rf = cfg.get("refund")
    if not isinstance(rf, dict) or rf.get("manual_review") is None:
        return True  # 旧行为：人工审核
    return _bool_of(rf, "manual_review", True)


# --------------------------------------------------------------------------- #
# 小程序配置（wechat_mini） -> 行为解释层
# --------------------------------------------------------------------------- #

async def mini_realname_enabled(db: AsyncSession) -> bool:
    """小程序「实名认证功能」开关：关闭后用户端不可提交实名认证。未配置默认开启（旧行为）。"""
    return await runtime_bool(db, "wechat_mini", "realname_enabled", True)
