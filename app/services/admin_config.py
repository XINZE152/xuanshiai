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
        {"platform_name": "宣智爱", "slogan": "你的爱值得被宣告", "pc_logo_url": None,
         "pc_guide_image_url": None, "douyin_qrcode_url": None,
         "home_share_title": "点击立即体验「宣智爱」本地实名社交婚恋平台",
         "home_share_summary": "一个有趣、有料、真实、优质的社交活动平台。",
         "wechat_push_summary": "点击底部+立即脱单！实名认证/线上相识/联谊活动/线下约见",
         "home_share_image_url": None, "wechat_login_logo_url": None,
         "login_slogan_url": None, "default_hometown": {"province": "江苏省", "city": "南京市"},
         "default_residence": {"province": "江苏省", "city": "南京市"},
         "default_native_place_text": "江苏省 / 南京市", "default_live_place_text": "江苏省 / 南京市",
         "default_avatar_male_url": None, "default_avatar_female_url": None},
        [],
    ),
    "platform_operation": (
        "平台运营模式", "平台整体运营模式与开放状态。",
        {"mode": "online-offline", "registration_enabled": True, "browse_enabled": True,
         "online_match_enabled": True, "membership_enabled": True, "matchmaker_enabled": True,
         "maintenance_message": "平台正在维护中，请稍后再试。"}, [],
    ),
    "platform_navigation": (
        "导航配置", "PC、H5、小程序和会员中心导航入口。",
        {"sections": [], "icon_rows": [], "pc": [], "h5": [], "mini_program": [], "member_center": []}, [],
    ),
    "platform_layout": (
        "平台布局", "手机端首页、会员资料页、红娘团队页与电脑端页面布局参数。",
        {"home": {}, "member": {}, "team": {}, "pc": {},
         "home_template": "default", "profile_template": "default", "modules": [], "banners": [], "popups": []}, [],
    ),
    "platform_register_guide": (
        "信息登记引导页配置", "会员登记流程前引导入口的标题、描述、展示与排序。",
        {"rows": []}, [],
    ),
    "platform_register_fields": (
        "基本资料登记配置", "会员资料登记页各字段的引导文案与注册流程开关。",
        {"subtitle": "", "fields": []}, [],
    ),
    "platform_private_fields": (
        "私密信息登记与展示配置", "私密资料项在注册、编辑与详情页中的展示开关及引导文案。",
        {"rows": []}, [],
    ),
    "platform_filter_config": (
        "筛选功能配置", "会员筛选条件的开关、排序与使用权限。",
        {"rows": []}, [],
    ),
    "platform_member_states": (
        "会员中心状态文案", "会员中心-状态设置中各状态的自定义名称与描述文案。",
        {"items": []}, [],
    ),
    "platform_custom_pages": (
        "自定义页面文案", "关于我们、私人定制、防骗提醒等自定义页面的标题与正文。",
        {"about_html": None, "custom_name": "私人定制", "custom_desc_html": None,
         "cheat_title": "防骗提醒", "cheat_html": None}, [],
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
    "member_auth": (
        "会员认证配置", "会员认证（M3-1）模块的快捷设置、承诺书与婚姻查询授权协议配置。",
        {"realname_force_id_card": False, "realname_fee": "0",
         "commitment_title": "单身承诺",
         "commitment_content": "本人使用昵称[[会员昵称]]，编号：[[相亲会员编号]]，在[[相亲平台名称]]登记婚姻交友信息，承诺所登记资料属实，承诺当前婚恋状态为[[婚姻状态]]，本人自行承担信息不属实造成的一切后果，与平台无关。",
         "marriage_agreement": "为保障婚恋交友平台信息真实性，维护健康诚信的交友环境，本人（授权人）自愿、真实、不可撤销地授权，依法依规查询本人婚姻状态信息，用于婚恋相亲资料核实。"},
        [],
    ),
    "system": (
        "系统配置", "管理员、广告、日志和系统运行规则。",
        {"basic": {"business_entity": None, "platform_name": "宣誓爱"}, "ad": {"enabled": False, "items": []},
         "audit_log_retention_days": 365, "admin_login_log_retention_days": 180}, [],
    ),
    "sys_site": (
        "系统配置-站点信息", "系统管理-系统配置页：经营主体、域名、备案、客服、Logo、协议与隐私等站点信息。",
        {"business_entity": None, "domain": "www.xuanshiai.com", "domain_icp": "苏ICP备2026018853号-3",
         "police_icp": None, "region": "江苏省-南京市", "service_phone": None, "service_wechat": None,
         "service_qrcode_url": None, "pc_footer_html": None, "admin_logo_url": None,
         "matchmaker_logo_url": None, "user_agreement_html": None, "privacy_policy_html": None}, [],
    ),
    "sys_access": (
        "系统配置-注册访问", "系统管理-系统配置页：平台浏览、注册、IP 限制与短信验证码开关。",
        {"browse_enabled": True, "secure_login": False, "register_enabled": True,
         "ip_type": "黑名单", "ip_text": None, "sms_captcha_enabled": True}, [],
    ),
    "sys_storage": (
        "系统配置-文件存储", "系统管理-系统配置页：私有化对象存储(七牛)密钥与地址。",
        {"qiniu_access_key": None, "qiniu_secret_key": None, "bucket": None,
         "upload_host": None, "remote_host": None, "private_queue": None}, [],
    ),
    "sys_payment": (
        "系统配置-支付配置", "系统管理-系统配置页：微信/支付宝支付商户参数与证书。",
        {"enabled": True, "provider": "wechat", "wechat_appid": None, "wechat_mch_id": None,
         "cert_type": "公钥模式", "wechat_pubkey_id": None, "wechat_pubkey": None,
         "wechat_api_v2_key": None, "wechat_api_v3_key": None, "p12_file": None, "sort": 1,
         "alipay_enabled": False}, [],
    ),
    "sys_watermark": (
        "系统配置-图片水印", "系统管理-系统配置页：图片水印开关、样式与参数。",
        {"enabled": True, "mode": "指定位置", "text": "宣智爱", "font": "微软雅黑",
         "scale": 40, "font_size": 0, "color": None, "rotate": 0, "opacity": 0,
         "fill_width": 400, "fill_height": 400}, [],
    ),
    "sys_posters": (
        "系统配置-海报配置", "系统管理-系统配置页：各场景分享海报及扫码关注公众号开关。",
        {"rows": []}, [],
    ),
    "sys_region": (
        "系统配置-自定义区域", "系统管理-系统配置页：区域数据管理（省份及下级区域）。",
        {"provinces": []}, [],
    ),
    "sys_ads": (
        "系统管理-广告位", "系统管理-广告管理页：H5/小程序各广告位的图、类型与开关。",
        {"rows": []}, [],
    ),
    "sys_outbound": (
        "系统管理-电话外呼平台", "系统管理-外呼平台页：外呼服务商与账户、呼叫中心地址。",
        {"provider": None, "account_name": None, "call_center_url": None,
         "record_download_url": None}, [],
    ),
    "sys_sms": (
        "系统管理-短信配置", "系统管理-短信：签名与全部通知场景开关（仅本地配置存储，发送走服务商）。",
        {"signature": None, "send_enabled": True, "notices": []}, [],
    ),
    # ---------------- 电子合同（财务管理-合同管理/模板/印章/合同配置） ----------------
    "econtract_config": (
        "电子合同配置", "财务管理-合同配置页：电子合同关键键值配置。",
        {"items": [
            {"id": 1, "key": "sign_expire_days", "name": "合同签署有效期（天）", "value": "7", "description": "超期未签署的合同自动置为已过期", "update_time": None},
            {"id": 2, "key": "expire_remind_days", "name": "到期提醒（天）", "value": "3", "description": "合同到期前 N 天提醒签署人", "update_time": None},
            {"id": 3, "key": "default_contract_type", "name": "默认合同类型", "value": "红娘服务协议", "description": "新发起合同的默认类型", "update_time": None},
            {"id": 4, "key": "allow_revoke", "name": "是否允许撤销签署", "value": "no", "description": "yes = 已签署合同可由管理员撤销", "update_time": None},
        ]}, [],
    ),
    "econtract_records": (
        "电子合同记录", "财务管理-合同管理页：合同签署记录（占位数据源，待电子签服务商接入）。",
        {"items": []}, [],
    ),
    "econtract_templates": (
        "电子合同模板", "财务管理-模板管理页：合同模板（占位数据源）。",
        {"items": []}, [],
    ),
    "econtract_seals": (
        "电子印章", "财务管理-印章管理页：印章图与启停（占位数据源）。",
        {"items": []}, [],
    ),
    # ---------------- 电话外呼记录（系统管理） ----------------
    "outbound_seats": (
        "外呼坐席", "系统管理-外呼状态页：坐席工号/状态/外呼号码/绑定红娘及统计（占位数据源，待外呼服务商接入）。",
        {"items": []}, [],
    ),
    "outbound_call_records": (
        "外呼呼叫记录", "系统管理-呼叫记录页：通话记录与录音地址（占位数据源）。",
        {"items": []}, [],
    ),
    # ---------------- 短信运营记录（系统管理） ----------------
    "sms_broadcasts": (
        "短信群发任务", "系统管理-短信群发页：群发任务与状态（占位数据源，实际下发走短信服务商）。",
        {"items": []}, [],
    ),
    "sms_send_records": (
        "短信发送记录", "系统管理-发送记录页：发送明细与余量统计（占位数据源）。",
        {"items": [], "balance": 0, "provider": "腾讯云专线"}, [],
    ),
    # ---------------- 公众号（后端暂无公众号平台对接，先落配置存储） ----------------
    "wechat_mp": (
        "公众号参数配置", "公众号-参数配置页：公众号凭据、加密模式、二维码与安全验证文件。",
        {"wx_no": "", "app_id": "", "app_secret": "", "api_token": "", "encoding_aes_key": "",
         "crypto_mode": "safe", "qrcode_url": None, "verify_file_name": "", "verify_file_url": None,
         "platform_templates": []}, [],
    ),
    "wechat_mp_fans": (
        "公众号关注粉丝", "公众号-关注粉丝页：粉丝列表（占位数据源，待公众号平台同步接口）。",
        {"items": []}, [],
    ),
    "wechat_mp_menu": (
        "公众号菜单", "公众号-菜单配置页：一级/二级菜单与发布时间。",
        {"top_menus": [], "sub_menus": [], "published_at": None}, [],
    ),
    "wechat_mp_replies": (
        "公众号自动回复", "公众号-自动回复页：关注/关键词/消息回复内容与回复方式。",
        {"follow": {"mode": "all", "items": []}, "keyword": {"items": []},
         "message": {"mode": "all", "items": []}}, [],
    ),
    "wechat_mp_templates": (
        "公众号模板消息", "公众号-模板消息页：模板行配置与启停。",
        {"items": []}, [],
    ),
    "wechat_mp_broadcasts": (
        "公众号消息群发", "公众号-消息群发页：已创建群发消息（占位数据源）。",
        {"items": []}, [],
    ),
    # ---------------- 小程序 ----------------
    "wechat_mini": (
        "小程序参数配置", "小程序-参数配置页：开关、凭据、样式、小程序码/分享封面与实名认证功能开关。",
        {"enabled": True, "app_id": "", "app_secret": "", "bar_color": "#6a2fbf",
         "qrcode_url": None, "share_cover_url": None, "realname_enabled": True,
         "authorized": False}, [],
    ),
    # ---------------- 后台权限分组（账号权限实际挂在账号上，分组为管理端组织占位） ----------------
    "admin_groups": (
        "后台权限分组", "系统管理-权限分组页：用户组与其权限集合。",
        {"groups": []}, [],
    ),
    # ---------------- 运营工具/活动/商家/短视频等（2026-09 对标补齐） ----------------
    "tools_active": (
        "活动参数配置", "活动报名-参数配置：活动分类、报名设置与宣传图。",
        {"categories": [], "banner_url": None, "signup_tip": "", "allow_cancel": True, "max_signups_per_user": 1}, [],
    ),
    "tools_active_alliance": (
        "活动运营方案", "活动报名-运营方案：富文本说明内容。",
        {"content_html": "", "enabled": True}, [],
    ),
    "tools_merchant_alliance": (
        "商家联盟功能配置", "商家联盟-功能配置：商家/商品分类管理与联盟开关。",
        {"categories": [], "enabled": True, "join_tip": ""}, [],
    ),
    "tools_short_video": (
        "短视频参数配置", "短视频-参数配置：红包、评论、打赏与展示开关。",
        {"red_packet_enabled": True, "comment_enabled": True, "tip_enabled": True,
         "publish_review": False, "banner_url": None, "daily_publish_limit": 5}, [],
    ),
    "tools_free_pay": (
        "自由收款配置", "运营工具-自由收款：收款类目、收款项目与收款码。",
        {"categories": [], "items": [], "qrcode_url": None, "remark": ""}, [],
    ),
    "tools_member_zone": (
        "会员分区配置", "运营工具-会员分区：分区列表、条件与背景图。",
        {"zones": []}, [],
    ),
    "tools_generate_tool": (
        "推文助手配置", "运营工具-推文助手：生成模板与默认文案。",
        {"templates": [], "default_content": ""}, [],
    ),
    "tools_love_partner": (
        "合伙红娘功能配置", "合伙红娘-功能配置：加盟说明与开关。",
        {"content_html": "", "enabled": True, "apply_tip": ""}, [],
    ),
    "tools_partner_bonus": (
        "合伙红娘分成配置", "合伙红娘-分成配置：各级分成比例与奖励。",
        {"levels": [], "mode": "ratio", "default_ratio": 0}, [],
    ),
    "tools_customer_leads": (
        "客源线索功能配置", "客源线索-功能配置：线索分配、跟进与保护规则。",
        {"auto_assign": False, "protect_days": 30, "follow_up_tip": "",
         "abandon_days": 15, "daily_new_limit": 10,
         # 客源线索-功能配置页面字段（与前端 UI 一一对应）
         "name_prefix": "客源", "link_promoter_on_convert": True, "show_converted_in_lead": True}, [],
    ),
    "tools_interactive_function": (
        "互动消息功能设置", "运营工具-互动消息：消息类型开关与频率限制。",
        {"like_enabled": True, "greet_enabled": True, "gift_notice_enabled": True,
         "daily_push_limit": 20, "quiet_hours": ""}, [],
    ),
    "tools_interactive_content": (
        "互动消息内容设置", "运营工具-互动消息：各类消息文案模板。",
        {"templates": []}, [],
    ),
    "tools_column_config": (
        "搭子社群栏目配置", "运营工具-搭子社群：栏目标题、描述与分享封面。",
        {"title": "搭子社群", "description": "兴趣搭子、活动搭子，找同频的人。",
         "share_cover_url": None, "categories": []}, [],
    ),
    "tools_good_news": (
        "红娘喜讯栏目配置", "运营工具-红娘喜讯：栏目标题、描述、分享封面、宣传头图、喜讯分类与祝福语。",
        {"title": "红娘喜讯", "description": "Ta们都是在我们的介绍撮合下，从相识、到恋爱、到见父母、到订婚、到结婚生子！",
         "share_cover_mode": "default", "share_cover_url": None, "banner_url": None,
         "view_url": "/subpages/xixun/index",
         "categories": ["牵手成功", "恋爱生活", "已见父母", "已订婚", "已领证", "已办婚礼", "婚后生活", "锦旗飘扬"],
         "category_icons": [],
         "blessings": []}, [],
    ),
    "tools_branch": (
        "分站配置", "分店管理-分站配置：分站列表与当前启用分站。",
        {"current_site": "", "sites": []}, [],
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
