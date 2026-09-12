"""Versioned configuration snapshots for the administration console."""

import asyncio
import json
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
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
        "财务配置", "支付、提现、退款、自由收款、电子合同与积分/余额/充值规则。",
        # 字段与前端「财务管理-系统配置」页一一对应（M9-A 扩展）
        {"payment_mode": "mock",
         "payment_channels": [],
         "point_name": "金币",                  # 积分名称
         "point_ratio": 10,                     # 1元=10积分
         "balance_name": "余额",                # 余额名称
         "withdrawal": {
             "enabled": False, "min_amount": "0.00",
             "fee_mode": "none", "fee_rate": 1, "fee_threshold": "100",
         },
         "withdraw_methods": {
             "auto_wechat": {"enabled": False, "min_amount": "1", "max_amount": "500"},
             "manual_wechat": {"enabled": True, "min_amount": "1", "max_amount": "1000"},
             "manual_bank": {"enabled": True, "min_amount": "1", "max_amount": "1000"},
             "manual_alipay": {"enabled": True, "min_amount": "1", "max_amount": "1000"},
         },
         "recharge_packages": [
             {"name": "积分充值套餐1", "amount": "1", "points": 10},
             {"name": "积分充值套餐2", "amount": "200", "points": 2200},
             {"name": "积分充值套餐3", "amount": "300", "points": 4000},
             {"name": "积分充值套餐4", "amount": "400", "points": 6000},
             {"name": "积分充值套餐5", "amount": "500", "points": 7500},
             {"name": "积分充值套餐6", "amount": "600", "points": 9000},
         ],
         "refund": {"manual_review": True}, "free_payment": {"enabled": False},
         "e_contract": {"enabled": False}, "commission_rules": []},
        ["merchant_key", "private_key", "public_key"],
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
        "电子合同配置", "财务管理-合同配置页：电子合同总开关与关键键值配置。",
        # M9-A 扩展：前端「电子合同-合同配置」页需要腾讯电子签总开关 + 4 项键值
        {"enabled": False,
         "items": [
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
        "活动参数配置", "活动报名-参数配置：活动分类、自定义栏目名称、默认图与用户协议须知。",
        # 字段与前端「活动报名-参数配置」页一一对应
        {"categories": ["专场活动", "会面小聚", "相亲大会", "政企联谊", "免费活动"],
         "column_name": "同城活动", "default_image_wide": None, "default_image_square": None,
         "agreement_html": ""}, [],
    ),
    "tools_active_alliance": (
        "活动运营方案", "活动报名-运营方案：富文本说明内容。",
        {"content_html": "", "enabled": True}, [],
    ),
    "tools_merchant_alliance": (
        "商家联盟功能配置", "商家联盟-功能配置：栏目标题/描述、分享封面、宣传头图与购买须知。",
        # 字段与前端「商家联盟-功能配置」页一一对应
        {"title": "优选合作商城",
         "description": "精选同城优质服务定制产品套餐，为您的约会提供愉快的消费",
         "share_cover_mode": "default", "share_cover_url": None,
         "banner_url": None, "banners": [],
         "notice": "成功购买后请凭消费券号至消费二维码前往商家消费\n您的短信中将收到券号，可在\"订单-中查看订单和二维码\n消费过程中若遇到使用问题请及时联系我们介入沟通",
         "view_url": "https://www.xuanshi.com/subpages/hezuo/index"}, [],
    ),
    "tools_short_video": (
        "短视频参数配置", "短视频-参数配置：审核开关、白名单、热门阈值、打赏范围、红包倒计时与发布协议。",
        # 字段与前端「短视频-参数配置」页一一对应
        {"column_name": "脱单加油站", "share_image_url": None,
         "share_title": "脱单干货",
         "share_summary": "了解我们，分享脱单干货和直播高光片段，回顾精彩活动，认识优质嘉宾",
         "normal_post_review": True, "normal_comment_review": False,
         "verified_post_review": True, "verified_comment_review": False,
         "whitelist": "", "hot_view_threshold": 100, "new_video_days": 7,
         "tip_min": 1, "tip_max": 100, "red_packet_countdown": 10,
         "publish_agreement_html": ""}, [],
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
        # share_bonus：合伙人同时是自己团队中的推广红娘时，是否享有该推广红娘的
        # 注册会员奖励与消费分成（前端功能配置页单选「享有/不享有」）。
        {"content_html": "", "enabled": True, "apply_tip": "", "share_bonus": True}, [],
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
        # 字段与前端「互动消息-功能设置」页一一对应
        {"system_enabled": True,            # 开启「会员消息系统」总开关
         "allow_unverified": False,         # 非实名认证会员是否允许使用消息功能
         "allow_non_vip_send": True,        # 非 VIP 是否允许主动发送
         "non_vip_daily_limit": 3,          # 非 VIP 每日条数（0 不限）
         "vip_daily_limit": 10,             # VIP 每日条数（0 不限）
         "allow_non_vip_reply": True,       # 非 VIP 是否允许回复
         "max_before_reply": 2},            # 对方未回复前最多发送条数（0 不限）
        [],
    ),
    "tools_interactive_content": (
        "互动消息内容设置", "运营工具-互动消息：各类消息文案模板。",
        # 字段与前端「互动消息-内容设置」页一一对应
        {"templates": [
            {"id": 1, "content": "你好，刚到你感觉很有眼缘，想简单聊两句互相了解下",
             "reply_count": 2, "enabled": True, "show_up": False, "sort_order": 100},
            {"id": 2, "content": "你看去过好多城市旅行，印象最好的目的地是哪里呀？",
             "reply_count": 0, "enabled": True, "show_up": True, "sort_order": 90},
            {"id": 3, "content": "你好，抱着认真找对象的心态，看你的规划和我很契合，想沟通了解下",
             "reply_count": 0, "enabled": True, "show_up": True, "sort_order": 80},
            {"id": 4, "content": "你好，我看到了你资料，感觉咱俩择偶要求各方面都很匹配，想跟你进一步了解下可以吗？",
             "reply_count": 3, "enabled": False, "show_up": True, "sort_order": 70},
            {"id": 5, "content": "你看过你的资料，觉得我们挺合拍的，希望我们能进一步了解更多",
             "reply_count": 3, "enabled": True, "show_up": True, "sort_order": 60},
        ]}, [],
    ),
    "tools_column_config": (
        "搭子社群栏目配置", "运营工具-搭子社群：栏目标题、描述与分享封面。",
        # 字段与前端「搭子社群-栏目配置」页一一对应
        {"title": "找搭子",
         "description": "年轻人的潮流新社交。放下手机，遇见真实的Ta",
         "share_cover_mode": "system",         # system / custom
         "share_cover_url": None,              # 自定义封面图（300x300）
         "banner_mode": "system",              # system / custom
         "banner_url": None,                   # 自定义首页头图（778x417）
         "promotion_intro_html": "",           # 推广介绍（富文本）
         "notice_html": "",                    # 入群须知（富文本）
         "agreement_html": "",                 # 入群协议（富文本）
         "hot_regions": []},                   # 热门区域 [{region, sort}]
        [],
    ),
    "tools_good_news": (
        "红娘喜讯栏目配置", "运营工具-红娘喜讯：栏目标题、描述、分享封面、宣传头图、喜讯分类与祝福语。",
        # 字段与前端「红娘喜讯-栏目配置」页一一对应
        {"title": "脱单喜讯",
         "description": "脱单喜讯",
         "share_cover_mode": "custom",         # system / custom
         "share_cover_url": None,              # 自定义封面图（300x300）
         "banner_url": None,                   # 宣传头图（698x240）
         "view_url": "https://www.xuanshiai.com/subpages/xixun/index",
         # 喜讯分类（顺序敏感，最后一项「锦旗飘扬」固定不可下移）
         "categories": [
             {"label": "牵手成功", "value": "牵手成功", "icon_url": None, "can_down": True, "sort_order": 100},
             {"label": "恋爱生活", "value": "恋爱生活", "icon_url": None, "can_down": True, "sort_order": 90},
             {"label": "已见父母", "value": "已见父母", "icon_url": None, "can_down": True, "sort_order": 80},
             {"label": "已订婚",   "value": "已订婚",   "icon_url": None, "can_down": True, "sort_order": 70},
             {"label": "已领证",   "value": "已领证",   "icon_url": None, "can_down": True, "sort_order": 60},
             {"label": "已办婚礼", "value": "已办婚礼", "icon_url": None, "can_down": True, "sort_order": 50},
             {"label": "婚后生活", "value": "婚后生活", "icon_url": None, "can_down": True, "sort_order": 40},
             {"label": "锦旗飘飘", "value": "锦旗飘飘", "icon_url": None, "can_down": False, "sort_order": 30},
         ],
         "blessings": [
             "恭喜这位孤寡青蛙成功上岸！从此下雨有人撑伞，吃火锅有人递纸。愿你们往后的日子，眼里有光，心里有爱，身边有彼此。",
             "终于有人把你这个人间宝藏捡回家啦！祝你们在平淡生活里，也能把日子过成糖。",
             "国家分配的CP终于到货了！祝您在这个看脸的世界里，不仅收获颜值，更收获满满的幸福。",
             "叮！您的单身贵族体验卡已到期，系统自动为您续费双人甜蜜套餐。愿往后余生，酸甜苦辣都有他/她陪。",
             "恭喜解锁人生新地图——恋爱副本！愿你们在这个快节奏的时代里，慢慢喜欢，慢慢相爱。",
         ]},
        [],
    ),
    "tools_branch": (
        "分站配置", "分店管理-分站配置：分站模式、分站列表与当前启用分站。",
        {"mode": "all", "current_site": "", "sites": []}, [],
    ),
    "tools_sales_match": (
        "销售匹配库功能配置", "运营工具-销售匹配库：头部宣传图、导航与功能控制、资料展示开关。",
        {"show_banner": True,
         "banner_url": None,
         "nav_items": [
             {"id": 1, "label": "嘉宾海选", "on": True},
             {"id": 2, "label": "红娘推荐", "on": True},
             {"id": 3, "label": "智能匹配", "on": True},
             {"id": 4, "label": "眼缘人选", "on": True},
         ],
         "show_auth": True,
         "show_intro": True,
         "show_mate_req": True,
         "show_material": True,
         "show_person_intro": True}, [],
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
    """确保 DEFAULT_CONFIGS 中所有 namespace 在 DB 存在，并补齐缺失字段。

    并发安全设计（修复 MySQL 1213 死锁）：
    1. **GET_LOCK 串行化**：获取 MySQL 命名锁 `ensure_admin_config_defaults`（5s 超时），
       所有并发请求被强制串行执行。避免两个事务同时 INSERT 同一 namespace 触发死锁。
    2. **SELECT 一次** 全部已存在 namespace，避免 50+ 次单条 SELECT。
    3. **ON DUPLICATE KEY UPDATE** 替代 INSERT IGNORE — 行为更确定，死锁概率更低。
    4. **死锁重试**：捕获 1213 异常最多 3 次（指数退避），兜底网络抖动。
    5. 锁失败 → 降级：直接 SELECT 已存在行，不阻塞请求。
    """
    lock_acquired = False
    try:
        result = (await db.execute(
            text("SELECT GET_LOCK('ensure_admin_config_defaults', 5)")
        )).scalar()
        lock_acquired = bool(result)
    except Exception:
        lock_acquired = False

    try:
        for attempt in range(3):
            try:
                existing_rows = (await db.execute(
                    text("SELECT namespace, config_json FROM admin_config_snapshot")
                )).mappings().all()
                existing_map = {row["namespace"]: row["config_json"] for row in existing_rows}

                inserts: list[dict[str, Any]] = []
                updates: list[dict[str, Any]] = []
                for namespace, (name, description, config, sensitive_keys) in DEFAULT_CONFIGS.items():
                    if namespace not in existing_map:
                        inserts.append({
                            "namespace": namespace,
                            "name": name,
                            "description": description,
                            "config_json": json.dumps(config, ensure_ascii=False),
                            "sensitive_keys_json": json.dumps(sensitive_keys, ensure_ascii=False),
                        })
                        continue
                    try:
                        current = json.loads(existing_map[namespace] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        current = {}
                    if isinstance(current, dict):
                        missing = {key: value for key, value in config.items() if key not in current}
                        if missing:
                            current.update(missing)
                            updates.append({
                                "namespace": namespace,
                                "config_json": json.dumps(current, ensure_ascii=False),
                            })

                # ON DUPLICATE KEY UPDATE 占位（namespace=VALUES(namespace)）—— 比 INSERT IGNORE
                # 死锁概率低，行为更确定。
                for params in inserts:
                    await db.execute(text("""INSERT INTO admin_config_snapshot
                        (namespace, name, description, version, config_json, sensitive_keys_json)
                        VALUES (:namespace, :name, :description, 1, :config_json, :sensitive_keys_json)
                        ON DUPLICATE KEY UPDATE namespace = VALUES(namespace)"""), params)
                for params in updates:
                    await db.execute(
                        text("""UPDATE admin_config_snapshot SET config_json = :config_json
                            WHERE namespace = :namespace"""),
                        params,
                    )
                if inserts or updates:
                    await db.commit()
                return
            except OperationalError as exc:
                try:
                    await db.rollback()
                except Exception:
                    pass
                if "1213" in str(exc) and attempt < 2:
                    await asyncio.sleep(0.1 * (2 ** attempt))
                    continue
                raise
    finally:
        if lock_acquired:
            try:
                await db.execute(text("SELECT RELEASE_LOCK('ensure_admin_config_defaults')"))
                await db.commit()
            except Exception:
                try:
                    await db.rollback()
                except Exception:
                    pass


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
