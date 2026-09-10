"""Versioned configuration snapshots for the administration console."""

import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin_config import AdminConfigAuditItem, AdminConfigAuditPage, AdminConfigSnapshot, AdminConfigUpdate

NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

DEFAULT_CONFIGS: dict[str, tuple[str, str, dict[str, Any], list[str]]] = {
    "platform_basic": (
        "平台基本配置", "平台品牌、分享、默认地区和默认头像。",
        {"platform_name": "宣誓爱", "slogan": "你的爱值得郑重宣告", "pc_logo_url": None,
         "pc_guide_image_url": None, "douyin_qrcode_url": None,
         "home_share_title": "点击立即体验『宣誓爱』本地实名社交婚恋平台",
         "home_share_summary": "一个有趣、有料、真实、优质的社交活动平台。",
         "wechat_push_summary": "点击底部+立即脱单！实名认证/线上相识/联谊活动/线下约见",
         "home_share_image_url": None, "wechat_login_logo_url": None,
         "login_slogan_url": None, "default_hometown": {"province": "江苏省", "city": "南京市"},
         "default_residence": {"province": "江苏省", "city": "南京市"},
         "default_avatar_male_url": None, "default_avatar_female_url": None},
        [],
    ),
    "platform_operation": (
        "平台运营模式", "平台开放状态和维护提示。",
        {"mode": "normal", "registration_enabled": True, "browse_enabled": True,
         "online_match_enabled": True, "membership_enabled": True, "matchmaker_enabled": True,
         "maintenance_message": "平台正在维护中，请稍后再试。"}, [],
    ),
    "platform_navigation": (
        "导航配置", "PC、H5、小程序和会员中心导航入口。",
        {"pc": [], "h5": [], "mini_program": [], "member_center": []}, [],
    ),
    "platform_layout": (
        "平台布局", "首页和资料页模块开关、排序及展示参数。",
        {"home_template": "default", "profile_template": "default", "modules": [], "banners": [], "popups": []}, [],
    ),
    "platform_permissions": (
        "平台权限配置", "浏览、资料查看、牵线和会员状态权限。",
         {"default_show_gender": "opposite", "incomplete_browse_pages": 2,
         "free_browse_daily_limit": 8, "high_match_browse_bonus": 5,
         "free_apply_daily_limit": 3, "vip_apply_daily_limit": 10,
         "free_superlike_daily_limit": 1, "vip_superlike_daily_limit": 3,
         "paper_plane_daily_limit": 3,
         "incomplete_profile_access": False, "show_matched_members": True,
         "non_vip_photo_limit": 1, "non_vip_video_limit": 0,
         "voice_access": "all_members", "matchmaker_note_access": "all_members",
         "partner_requirement_access": "all_members", "personal_info_access": "all_members",
         "self_description_access": "all_members", "more_info_access": "all_members",
         "entrust_matchmaker_min_vip": "diamond", "private_min_vip": "diamond",
         "allow_member_stop_service": True, "allow_member_mark_single": True,
         "allow_unverified_start_match": False, "allow_unsigned_start_match": True,
         "online_match_daily_limit": 0, "allow_unverified_be_matched": False,
         "require_id_card_photo": False, "show_exclusive_matchmaker": True,
         "allow_same_id_multiple_accounts": False, "require_avatar": True,
         "require_photo": False, "allow_member_edit_marriage": True, "allow_member_edit_education": True}, [],
    ),
    "platform_content": (
        "平台内容配置", "协议、公告、引导和运营文案。",
        {"user_agreement": None, "privacy_policy": None, "safety_pledge": None,
         "membership_agreement": None, "realname_notice": None, "home_notice": None,
         "registration_guide": None, "match_notice": None, "maintenance_message": "平台正在维护中，请稍后再试。",
         "customer_service_phone": None, "seo": {"title": "宣誓爱", "keywords": "婚恋,交友", "description": ""}}, [],
    ),
    "platform_base_data": (
        "平台基础数据", "会员资料、认证、标签、活动和业务状态字典。",
        {"gender": [], "marriage_status": [], "education": [], "occupation": [], "income_ranges": [],
         "height_ranges": [], "ethnicity": [], "constellation": [], "mbti": [], "interests": [],
         "member_tags": [], "member_statuses": [], "certification_types": [], "lead_sources": [],
         "activity_types": [], "matchmaker_levels": [], "vip_levels": []}, [],
    ),
    "platform_pay": (
        "平台收费配置", "会员、权益、认证、活动和服务收费规则。",
        {"currency": "CNY", "amount_unit": "yuan", "membership_packages": [], "point_products": [],
         "realname_verification": {"enabled": False, "price": "0.00"},
         "marriage_status_query": {"enabled": False, "price": "0.00"},
         "e_contract": {"enabled": False, "price": "0.00"}, "commission_rules": [],
         "refund_policy": {"enabled": True, "manual_review": True}}, [],
    ),
    "wechat": (
        "公众号配置", "公众号接入、菜单、自动回复和模板消息配置。",
        {"wechat_id": None, "app_id": None, "app_secret": None, "api_token": None,
         "encoding_aes_key": None, "encode_type": "安全模式", "qrcode_url": None,
         "menus": [], "auto_replies": [], "templates": [], "send_enabled": False},
        ["app_secret", "api_token", "encoding_aes_key"],
    ),
    "miniprogram": (
        "小程序配置", "小程序登录、订阅消息和首页配置。",
        {"app_id": None, "app_secret": None, "login_enabled": True, "subscribe_templates": [], "home_modules": []},
        ["app_secret"],
    ),
    "sms": (
        "短信配置", "短信供应商、签名、模板和发送开关。",
        {"provider": "disabled", "signatures": [], "templates": [], "send_enabled": False,
         "daily_limit": 10, "send_interval_seconds": 60}, ["access_key", "access_secret"],
    ),
    "finance": (
        "财务配置", "支付、提现、退款、自由收款和电子合同规则。",
        {"payment_mode": "mock", "payment_channels": [], "withdrawal": {"enabled": False, "min_amount": "0.00"},
         "refund": {"manual_review": True}, "free_payment": {"enabled": False}, "e_contract": {"enabled": False},
         "commission_rules": []}, ["merchant_key", "private_key", "public_key"],
    ),
    "merchant": (
        "商家联盟配置", "商家入驻、审核、核销和佣金规则。",
        {"enabled": False, "audit_required": True, "categories": [], "commission_rules": [], "verification": {"enabled": False}}, [],
    ),
    "short_video": (
        "短视频配置", "视频发布、审核、推荐、红包和打赏规则。",
        {"enabled": False, "review_required": True, "max_duration_seconds": 60, "max_size_mb": 100,
         "recommendation": {"enabled": False}, "red_packet": {"enabled": False}, "tip": {"enabled": False}}, [],
    ),
    "matchmaker": (
        "红娘业务配置", "会员和客源分派、弃海及红娘分成规则。",
        {"assignment": {"member_crm": {"strategy": "designated"}, "customer_lead": {"strategy": "designated"}},
         "abandon": {"member_crm": {"days": 0, "daily_pickup_limit": 0}, "customer_lead": {"days": 0, "daily_pickup_limit": 0}},
         "commission_rules": []}, [],
    ),
    "system": (
        "系统配置", "管理员、广告、日志和系统运行规则。",
        {"basic": {"business_entity": None, "platform_name": "宣誓爱"}, "ad": {"enabled": False, "items": []},
         "audit_log_retention_days": 365, "admin_login_log_retention_days": 180}, [],
    ),
}


def _validate_namespace(namespace: str) -> None:
    if not NAMESPACE_PATTERN.fullmatch(namespace):
        raise HTTPException(422, detail="namespace 只能使用小写字母、数字和下划线，长度为2-64")


def _mask(value: Any, prefix: str, sensitive_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {key: ("******" if key in sensitive_keys else _mask(item, f"{prefix}.{key}", sensitive_keys)) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask(item, prefix, sensitive_keys) for item in value]
    return value


def _contains_plain_sensitive(value: Any, sensitive_keys: set[str]) -> bool:
    """Reject plaintext secret values instead of persisting them in snapshots."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in sensitive_keys and item not in (None, "", "******"):
                return True
            if _contains_plain_sensitive(item, sensitive_keys):
                return True
    elif isinstance(value, list):
        return any(_contains_plain_sensitive(item, sensitive_keys) for item in value)
    return False


def _snapshot(row: Any, *, mask_sensitive: bool = True) -> AdminConfigSnapshot:
    sensitive = list(json.loads(row["sensitive_keys_json"] or "[]"))
    config = json.loads(row["config_json"])
    if mask_sensitive:
        config = _mask(config, "", set(sensitive))
    return AdminConfigSnapshot(namespace=row["namespace"], name=row["name"], description=row["description"],
                               version=int(row["version"]), config=config, sensitive_keys=sensitive,
                               updated_by=row["updated_by"], updated_at=row["updated_at"])


async def ensure_defaults(db: AsyncSession) -> None:
    for namespace, (name, description, config, sensitive_keys) in DEFAULT_CONFIGS.items():
        await db.execute(text("""INSERT IGNORE INTO admin_config_snapshot
            (namespace, name, description, version, config_json, sensitive_keys_json)
            VALUES (:namespace, :name, :description, 1, :config_json, :sensitive_keys_json)"""), {
            "namespace": namespace, "name": name, "description": description,
            "config_json": json.dumps(config, ensure_ascii=False),
            "sensitive_keys_json": json.dumps(sensitive_keys, ensure_ascii=False),
        })
        existing = (await db.execute(
            text("SELECT config_json FROM admin_config_snapshot WHERE namespace=:namespace"),
            {"namespace": namespace},
        )).mappings().first()
        if existing:
            try:
                current = json.loads(existing["config_json"])
            except (TypeError, json.JSONDecodeError):
                current = {}
            if isinstance(current, dict):
                missing = {key: value for key, value in config.items() if key not in current}
                if missing:
                    current.update(missing)
                    await db.execute(
                        text("UPDATE admin_config_snapshot SET config_json=:config_json WHERE namespace=:namespace"),
                        {"namespace": namespace, "config_json": json.dumps(current, ensure_ascii=False)},
                    )
    await db.commit()


async def get_config(db: AsyncSession, namespace: str) -> AdminConfigSnapshot:
    _validate_namespace(namespace)
    await ensure_defaults(db)
    row = (await db.execute(text("SELECT * FROM admin_config_snapshot WHERE namespace=:namespace"), {"namespace": namespace})).mappings().first()
    if not row:
        raise HTTPException(404, detail="配置域不存在")
    return _snapshot(row)


async def get_runtime_value(db: AsyncSession, namespace: str, key: str, fallback: Any) -> Any:
    """Read one live value while retaining the legacy default as fallback."""
    _validate_namespace(namespace)
    if not isinstance(db, AsyncSession):
        return fallback
    try:
        result = await db.execute(
            text("SELECT config_json FROM admin_config_snapshot WHERE namespace=:namespace"),
            {"namespace": namespace},
        )
        row = result.mappings().first() if result is not None else None
    except (AttributeError, TypeError):
        # Keep unit-test doubles and pre-migration installations on legacy defaults.
        row = None
    if not row:
        return fallback
    try:
        value = json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value.get(key, fallback) if isinstance(value, dict) else fallback


async def update_config(db: AsyncSession, admin_id: int, namespace: str, request: AdminConfigUpdate) -> AdminConfigSnapshot:
    _validate_namespace(namespace)
    await ensure_defaults(db)
    row = (await db.execute(text("SELECT * FROM admin_config_snapshot WHERE namespace=:namespace FOR UPDATE"), {"namespace": namespace})).mappings().first()
    if not row:
        raise HTTPException(404, detail="配置域不存在")
    if int(row["version"]) != request.version:
        raise HTTPException(409, detail="配置版本已变化，请重新读取后再提交")
    sensitive_keys = set(json.loads(row["sensitive_keys_json"] or "[]"))
    if _contains_plain_sensitive(request.config, sensitive_keys):
        raise HTTPException(422, detail="敏感配置必须通过环境变量或密钥管理系统注入，接口不接受明文")
    before = json.loads(row["config_json"])
    next_version = int(row["version"]) + 1
    await db.execute(text("""UPDATE admin_config_snapshot SET version=:version, config_json=:config_json,
        updated_by=:updated_by, updated_at=UTC_TIMESTAMP() WHERE namespace=:namespace"""), {
        "namespace": namespace, "version": next_version,
        "config_json": json.dumps(request.config, ensure_ascii=False), "updated_by": admin_id,
    })
    await db.execute(text("""INSERT INTO admin_config_audit_log
        (namespace, version, action, actor_user_id, change_summary, before_config_json, after_config_json)
        VALUES (:namespace, :version, 'update', :actor, :summary, :before, :after)"""), {
        "namespace": namespace, "version": next_version, "actor": admin_id, "summary": request.change_summary,
        "before": json.dumps(before, ensure_ascii=False), "after": json.dumps(request.config, ensure_ascii=False),
    })
    await db.commit()
    return await get_config(db, namespace)


async def list_audits(db: AsyncSession, namespace: str, page: int, page_size: int) -> AdminConfigAuditPage:
    _validate_namespace(namespace)
    offset = (page - 1) * page_size
    total = int((await db.execute(text("SELECT COUNT(*) FROM admin_config_audit_log WHERE namespace=:namespace"), {"namespace": namespace})).scalar() or 0)
    rows = (await db.execute(text("""SELECT id, namespace, version, action, actor_user_id, change_summary,
        before_config_json, after_config_json, created_at FROM admin_config_audit_log
        WHERE namespace=:namespace ORDER BY id DESC LIMIT :limit OFFSET :offset"""),
        {"namespace": namespace, "limit": page_size, "offset": offset})).mappings().all()
    items = [AdminConfigAuditItem(id=int(row["id"]), namespace=row["namespace"], version=int(row["version"]),
        action=row["action"], actor_user_id=int(row["actor_user_id"]), change_summary=row["change_summary"],
        before_config=json.loads(row["before_config_json"]) if row["before_config_json"] else None,
        after_config=json.loads(row["after_config_json"]) if row["after_config_json"] else None,
        created_at=row["created_at"]) for row in rows]
    return AdminConfigAuditPage(items=items, page=page, page_size=page_size, total=total, has_more=page * page_size < total)
