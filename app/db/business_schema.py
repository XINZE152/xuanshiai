"""一期商业化和组织归属领域的数据库表定义。"""

BUSINESS_TABLES = {
    "admin_sms_statistics": """
        CREATE TABLE IF NOT EXISTS `admin_sms_statistics` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `success_count` bigint unsigned NOT NULL DEFAULT 0,
            `failed_count` bigint unsigned NOT NULL DEFAULT 0,
            `remaining_count` bigint unsigned NOT NULL DEFAULT 0,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_admin_sms_tenant` (`tenant_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理端短信资源汇总；余额以账本任务同步写入'
    """,
    "admin_academy_category": """
        CREATE TABLE IF NOT EXISTS `admin_academy_category` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `parent_id` bigint unsigned DEFAULT NULL,
            `name` varchar(128) NOT NULL,
            `description` varchar(500) DEFAULT NULL,
            `image` varchar(500) DEFAULT NULL,
            `category_type` varchar(32) NOT NULL DEFAULT 'Guides',
            `sort` int NOT NULL DEFAULT 0,
            `enabled` tinyint NOT NULL DEFAULT 1,
            `matchmaker_class_enabled` tinyint NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`), KEY `idx_academy_tree` (`tenant_id`, `category_type`, `enabled`, `parent_id`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理端婚创学苑栏目'
    """,
    "admin_recharge_item": """
        CREATE TABLE IF NOT EXISTS `admin_recharge_item` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `name` varchar(128) NOT NULL,
            `resource_type` varchar(32) NOT NULL,
            `quantity` int unsigned NOT NULL,
            `price` decimal(12,2) NOT NULL,
            `sort` int NOT NULL DEFAULT 0,
            `enabled` tinyint NOT NULL DEFAULT 1,
            PRIMARY KEY (`id`), KEY `idx_recharge_visible` (`tenant_id`, `enabled`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='只读充值商品目录，不记录支付或余额'
    """,
    "admin_announcement_version": """
        CREATE TABLE IF NOT EXISTS `admin_announcement_version` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `name` varchar(64) NOT NULL,
            `is_first` tinyint NOT NULL DEFAULT 0,
            `published_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`), KEY `idx_announcement_version_published` (`tenant_id`, `published_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已发布更新报告版本'
    """,
    "admin_announcement": """
        CREATE TABLE IF NOT EXISTS `admin_announcement` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `version_id` bigint unsigned DEFAULT NULL,
            `category` varchar(64) NOT NULL DEFAULT '',
            `title` varchar(255) NOT NULL,
            `title_color` varchar(32) DEFAULT NULL,
            `title_bold` tinyint NOT NULL DEFAULT 0,
            `top` tinyint NOT NULL DEFAULT 0,
            `sort_order` int NOT NULL DEFAULT 0,
            `link_to` varchar(500) DEFAULT NULL,
            `published_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_announcement_list` (`tenant_id`, `published_at`, `category`, `top`, `sort_order`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理端更新公告'
    """,
    "admin_announcement_read": """
        CREATE TABLE IF NOT EXISTS `admin_announcement_read` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_id` bigint unsigned NOT NULL,
            `announcement_id` bigint unsigned NOT NULL,
            `read_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_announcement_read` (`account_id`, `announcement_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='按管理员记录公告已读状态'
    """,
    "admin_feedback_message": """
        CREATE TABLE IF NOT EXISTS `admin_feedback_message` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `ticket_id` bigint unsigned NOT NULL,
            `sender_type` varchar(16) NOT NULL COMMENT 'CUSTOMER/SERVICE/SYSTEM',
            `content` varchar(4000) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_feedback_last_message` (`tenant_id`, `ticket_id`, `created_at`, `id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工单消息；首页仅读取未读状态'
    """,
    "admin_feedback_read": """
        CREATE TABLE IF NOT EXISTS `admin_feedback_read` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_id` bigint unsigned NOT NULL,
            `feedback_id` bigint unsigned NOT NULL,
            `read_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_feedback_read` (`account_id`, `feedback_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员工单消息已读关系'
    """,
    "customer_lead": """
        CREATE TABLE IF NOT EXISTS `customer_lead` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `name` varchar(128) NOT NULL,
            `phone` varchar(32) DEFAULT NULL,
            `wechat` varchar(128) DEFAULT NULL,
            `source` varchar(64) NOT NULL,
            `intention_level` tinyint NOT NULL DEFAULT '1' COMMENT '1低 2中 3高',
            `status` varchar(32) NOT NULL DEFAULT 'NEW' COMMENT 'NEW/CONTACTED/INTENDED/CONVERTED/LOST/CLOSED',
            `active_phone` varchar(32) GENERATED ALWAYS AS (CASE WHEN `status` IN ('LOST','CLOSED') THEN NULL ELSE NULLIF(TRIM(`phone`), '') END) STORED COMMENT '有效手机号（弃海/关闭为NULL，唯一）',
            `active_wechat` varchar(128) GENERATED ALWAYS AS (CASE WHEN `status` IN ('LOST','CLOSED') THEN NULL ELSE NULLIF(TRIM(`wechat`), '') END) STORED COMMENT '有效微信（弃海/关闭为NULL，唯一）',
            `matchmaker_id` bigint unsigned DEFAULT NULL,
            `organization_id` bigint unsigned DEFAULT NULL,
            `promoter_id` bigint unsigned DEFAULT NULL COMMENT '推广红娘用户ID',
            `audit_status` varchar(16) NOT NULL DEFAULT 'active' COMMENT 'active有效/pending待核',
            `next_follow_at` datetime DEFAULT NULL,
            `converted_user_id` bigint unsigned DEFAULT NULL,
            `remark` varchar(2000) DEFAULT NULL,
            `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_customer_lead_active_phone` (`active_phone`),
            UNIQUE KEY `uk_customer_lead_active_wechat` (`active_wechat`),
            KEY `idx_customer_lead_promoter` (`promoter_id`),
            KEY `idx_customer_lead_status` (`status`, `created_at`),
            KEY `idx_customer_lead_matchmaker` (`matchmaker_id`, `status`),
            KEY `idx_customer_lead_phone` (`phone`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台客源线索'
    """,
    "customer_lead_follow_up": """
        CREATE TABLE IF NOT EXISTS `customer_lead_follow_up` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `lead_id` bigint unsigned NOT NULL,
            `method` varchar(32) NOT NULL COMMENT 'PHONE/WECHAT/VISIT/OTHER',
            `content` varchar(2000) NOT NULL,
            `intention_level` tinyint DEFAULT NULL,
            `next_follow_at` datetime DEFAULT NULL,
            `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_lead_follow_up_lead` (`lead_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客源线索跟进记录'
    """,
    "customer_lead_abandonment": """
        CREATE TABLE IF NOT EXISTS `customer_lead_abandonment` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `lead_id` bigint unsigned NOT NULL,
            `reason` varchar(500) NOT NULL,
            `abandoned_by` bigint unsigned NOT NULL,
            `abandoned_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `restored_by` bigint unsigned DEFAULT NULL,
            `restored_at` datetime DEFAULT NULL,
            `restore_reason` varchar(500) DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `idx_lead_abandonment_active` (`lead_id`, `restored_at`, `abandoned_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客源弃海和恢复审计记录'
    """,
    "customer_lead_review": """
        CREATE TABLE IF NOT EXISTS `customer_lead_review` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `lead_id` bigint unsigned NOT NULL,
            `status` varchar(16) NOT NULL COMMENT 'APPROVED/REJECTED', `reason` varchar(500) DEFAULT NULL,
            `reviewed_by` bigint unsigned NOT NULL, `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_lead_review` (`lead_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源审核历史'
    """,
    "customer_lead_call_note": """
        CREATE TABLE IF NOT EXISTS `customer_lead_call_note` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `lead_id` bigint unsigned NOT NULL,
            `content` varchar(200) NOT NULL, `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_lead_call_note` (`lead_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源通话小记'
    """,
    "customer_lead_tag": """
        CREATE TABLE IF NOT EXISTS `customer_lead_tag` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `name` varchar(64) NOT NULL, `color` varchar(16) DEFAULT NULL,
            `enabled` tinyint NOT NULL DEFAULT 1, `sort` int NOT NULL DEFAULT 0,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_customer_lead_tag` (`name`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源标签配置'
    """,
    "customer_lead_tag_relation": """
        CREATE TABLE IF NOT EXISTS `customer_lead_tag_relation` (
            `lead_id` bigint unsigned NOT NULL, `tag_id` bigint unsigned NOT NULL,
            PRIMARY KEY (`lead_id`, `tag_id`), KEY `idx_customer_lead_tag` (`tag_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源标签关联'
    """,
    "member_follow_up": """
        CREATE TABLE IF NOT EXISTS `member_follow_up` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `method` varchar(32) NOT NULL COMMENT 'PHONE/WECHAT/VISIT/OTHER',
            `content` varchar(2000) NOT NULL,
            `next_follow_at` datetime DEFAULT NULL,
            `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_member_follow_up_user` (`user_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员 CRM 跟进记录'
    """,
    "member_call_record": """
        CREATE TABLE IF NOT EXISTS `member_call_record` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `direction` varchar(16) NOT NULL DEFAULT 'OUTBOUND' COMMENT 'INBOUND/OUTBOUND',
            `status` varchar(16) NOT NULL DEFAULT 'COMPLETED' COMMENT 'COMPLETED/MISSED/FAILED',
            `duration_seconds` int unsigned NOT NULL DEFAULT 0,
            `remark` varchar(2000) DEFAULT NULL,
            `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_member_call_record_user` (`user_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员 CRM 通话记录'
    """,
    "matchmaker_workspace_profile": """
        CREATE TABLE IF NOT EXISTS `matchmaker_workspace_profile` (
            `user_id` bigint unsigned NOT NULL,
            `display_name` varchar(64) NOT NULL,
            `level` varchar(16) NOT NULL DEFAULT 'NORMAL' COMMENT 'NORMAL/SUPER，由平台配置超级红娘范围',
            `organization_id` bigint unsigned DEFAULT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1正常 2暂停',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`user_id`),
            KEY `idx_matchmaker_workspace_org` (`organization_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='移动端服务红娘工作台资料和数据范围'
    """,
    "matchmaker_member_review": """
        CREATE TABLE IF NOT EXISTS `matchmaker_member_review` (
            `user_id` bigint unsigned NOT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PASSED/REJECTED；仅公开资料审核',
            `reason` varchar(500) DEFAULT NULL,
            `reviewed_by` bigint unsigned DEFAULT NULL,
            `reviewed_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`user_id`),
            KEY `idx_matchmaker_member_review_status` (`status`, `reviewed_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='服务红娘公开资料审核，不替代实名认证或学历认证'
    """,
    "matchmaker_introduction": """
        CREATE TABLE IF NOT EXISTS `matchmaker_introduction` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `from_user_id` bigint unsigned NOT NULL,
            `to_user_id` bigint unsigned NOT NULL,
            `matchmaker_id` bigint unsigned NOT NULL,
            `organization_id` bigint unsigned DEFAULT NULL,
            `status` varchar(16) NOT NULL COMMENT 'PENDING/IN_PROGRESS/SUCCEEDED/FAILED',
            `failure_reason` varchar(500) DEFAULT NULL,
            `note` varchar(500) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_introduction_marker` (`matchmaker_id`, `note`),
            KEY `idx_matchmaker_introduction_scope` (`matchmaker_id`, `status`, `created_at`),
            KEY `idx_matchmaker_introduction_org` (`organization_id`, `status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='服务红娘牵线流程记录，与认识申请状态分离'
    """,
    "matchmaker_admin_account": """
        CREATE TABLE IF NOT EXISTS `matchmaker_admin_account` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `username` varchar(64) NOT NULL,
            `password_hash` varchar(255) NOT NULL,
            `matchmaker_user_id` bigint unsigned DEFAULT NULL,
            `display_name` varchar(128) NOT NULL,
            `data_scope` varchar(16) NOT NULL DEFAULT 'SELF' COMMENT 'SELF/STORE/ORGANIZATION/ALL',
            `organization_id` bigint unsigned DEFAULT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1正常 2停用',
            `failed_count` int unsigned NOT NULL DEFAULT '0',
            `locked_until` datetime DEFAULT NULL,
            `last_login_at` datetime DEFAULT NULL,
            `last_login_ip` varchar(64) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_admin_username` (`username`),
            KEY `idx_matchmaker_admin_user` (`matchmaker_user_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='独立红娘后台账号'
    """,
    "matchmaker_admin_session": """
        CREATE TABLE IF NOT EXISTS `matchmaker_admin_session` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_id` bigint unsigned NOT NULL,
            `refresh_token_hash` char(64) NOT NULL,
            `access_token_hash` char(64) DEFAULT NULL,
            `ip` varchar(64) DEFAULT NULL,
            `user_agent` varchar(255) DEFAULT NULL,
            `access_expire_at` datetime NOT NULL,
            `refresh_expire_at` datetime NOT NULL,
            `last_used_at` datetime NOT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1有效 2注销 3轮换',
            `revoked_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_admin_refresh` (`refresh_token_hash`),
            KEY `idx_matchmaker_admin_session_account` (`account_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘后台登录会话'
    """,
    "matchmaker_admin_permission": """
        CREATE TABLE IF NOT EXISTS `matchmaker_admin_permission` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_id` bigint unsigned NOT NULL,
            `permission` varchar(128) NOT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_admin_permission` (`account_id`, `permission`),
            KEY `idx_matchmaker_admin_permission_account` (`account_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='独立红娘后台账号权限'
    """,
    "matchmaker_admin_login_log": """
        CREATE TABLE IF NOT EXISTS `matchmaker_admin_login_log` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_id` bigint unsigned DEFAULT NULL,
            `username` varchar(64) NOT NULL,
            `login_status` tinyint NOT NULL COMMENT '0失败 1成功',
            `ip` varchar(64) DEFAULT NULL,
            `user_agent` varchar(255) DEFAULT NULL,
            `device_id` varchar(128) DEFAULT NULL,
            `failure_reason` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_matchmaker_admin_login_account` (`account_id`, `created_at`),
            KEY `idx_matchmaker_admin_login_username` (`username`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='独立红娘后台登录日志'
    """,
    "matchmaker_admin_member_note": """
        CREATE TABLE IF NOT EXISTS `matchmaker_admin_member_note` (
            `user_id` bigint unsigned NOT NULL,
            `note` varchar(2000) DEFAULT NULL,
            `updated_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='后台会员备注'
    """,
    "organization": """
        CREATE TABLE IF NOT EXISTS `organization` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `parent_id` bigint unsigned DEFAULT NULL,
            `org_type` varchar(32) NOT NULL COMMENT 'platform/store',
            `code` varchar(64) NOT NULL,
            `name` varchar(128) NOT NULL,
            `display_name` varchar(128) DEFAULT NULL,
            `region_code` varchar(64) DEFAULT NULL,
            `link_url` varchar(255) DEFAULT NULL COMMENT '分站访问链接',
            `sort_order` int NOT NULL DEFAULT '0' COMMENT '显示排序，数字越大越靠前',
            `qr_code` varchar(500) DEFAULT NULL COMMENT '分站链接/二维码图片地址',
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1正常 2关闭 3停用',
            `auto_redirect` tinyint NOT NULL DEFAULT '0',
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_organization_code` (`code`),
            KEY `idx_organization_parent_status` (`parent_id`, `status`),
            KEY `idx_organization_region` (`region_code`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='平台和门店组织'
    """,
    "organization_member": """
        CREATE TABLE IF NOT EXISTS `organization_member` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `organization_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL,
            `role_code` varchar(64) NOT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1有效 2暂停 3结束',
            `granted_by` bigint unsigned DEFAULT NULL,
            `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `ended_at` datetime DEFAULT NULL,
            `end_reason` varchar(255) DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_organization_member_role` (`organization_id`, `user_id`, `role_code`, `status`),
            KEY `idx_organization_member_user` (`user_id`, `status`),
            KEY `idx_organization_member_org` (`organization_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='组织成员关系历史'
    """,
    "resource_assignment": """
        CREATE TABLE IF NOT EXISTS `resource_assignment` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL COMMENT '被分派的会员/客源用户',
            `organization_id` bigint unsigned DEFAULT NULL,
            `matchmaker_id` bigint unsigned DEFAULT NULL,
            `source` varchar(32) NOT NULL DEFAULT 'manual',
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1生效 2结束',
            `assigned_by` bigint unsigned DEFAULT NULL,
            `effective_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `ended_at` datetime DEFAULT NULL,
            `end_reason` varchar(255) DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `idx_resource_assignment_user` (`user_id`, `status`),
            KEY `idx_resource_assignment_matchmaker` (`matchmaker_id`, `status`),
            KEY `idx_resource_assignment_org` (`organization_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员资源归属历史'
    """,
    "promotion_touch": """
        CREATE TABLE IF NOT EXISTS `promotion_touch` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `code` varchar(128) NOT NULL,
            `promoter_id` bigint unsigned DEFAULT NULL,
            `partner_team_id` bigint unsigned DEFAULT NULL,
            `registered_user_id` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `expires_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_promotion_touch_code` (`code`),
            KEY `idx_promotion_touch_promoter` (`promoter_id`),
            KEY `idx_promotion_touch_registered` (`registered_user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='推广触点'
    """,
    "promotion_attribution": """
        CREATE TABLE IF NOT EXISTS `promotion_attribution` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `promoter_id` bigint unsigned NOT NULL,
            `organization_id` bigint unsigned DEFAULT NULL,
            `touch_id` bigint unsigned DEFAULT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1有效 2结束 3作弊',
            `effective_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `ended_at` datetime DEFAULT NULL,
            `end_reason` varchar(255) DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_promotion_attribution_active` (`user_id`, `status`),
            KEY `idx_promotion_attribution_promoter` (`promoter_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会员推广归属'
    """,
    "promotion_order": """
        CREATE TABLE IF NOT EXISTS `promotion_order` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `order_no` varchar(64) NOT NULL COMMENT '支付订单号',
            `user_id` bigint unsigned NOT NULL COMMENT '购买推广人',
            `product_name` varchar(128) NOT NULL COMMENT '推广套餐名称',
            `amount` decimal(12,2) NOT NULL DEFAULT '0.00' COMMENT '订单金额',
            `pay_status` varchar(16) NOT NULL DEFAULT 'unpaid' COMMENT 'unpaid未支付/paid已支付/refunded已退款',
            `pay_method` varchar(32) DEFAULT NULL COMMENT 'wechat/alipay/balance/offline',
            `status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending待处理/processing推广中/done已完成/cancelled已取消',
            `remark` varchar(500) DEFAULT NULL,
            `paid_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_promotion_order_no` (`order_no`),
            KEY `idx_promotion_order_user` (`user_id`, `created_at`),
            KEY `idx_promotion_order_status` (`status`, `pay_status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='推广服务订单'
    """,
    "partner_team": """
        CREATE TABLE IF NOT EXISTS `partner_team` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `owner_user_id` bigint unsigned NOT NULL,
            `name` varchar(128) NOT NULL,
            `level_id` tinyint unsigned NOT NULL DEFAULT '1' COMMENT '合伙级别：1 初级 / 2 中级 / 3 战略合伙人（固定 3 种）',
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1正常 2关闭 3冻结',
            `open_mode` varchar(32) NOT NULL DEFAULT 'manual' COMMENT 'manual/paid',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_partner_team_owner` (`owner_user_id`),
            KEY `idx_partner_team_status` (`status`),
            KEY `idx_partner_team_level` (`level_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='合伙人团队'
    """,
    "partner_level_config": """
        CREATE TABLE IF NOT EXISTS `partner_level_config` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `level_id` tinyint unsigned NOT NULL COMMENT '业务级别：1 初级 / 2 中级 / 3 战略合伙人（固定 3 种）',
            `level_name` varchar(32) NOT NULL COMMENT '级别名称',
            `auto_split_mode` varchar(16) NOT NULL DEFAULT 'auto_rate' COMMENT 'fixed_amount 自定义固定金额 / auto_rate 按比例自动计算',
            `auto_split_rate` decimal(7,4) DEFAULT NULL COMMENT '按比例自动计算的比例(%)，auto_split_mode=auto_rate 时生效',
            `promote_performance_threshold` decimal(12,2) DEFAULT NULL COMMENT '自动升级条件：团队累计业绩阈值(元)',
            `promote_member_threshold` int DEFAULT NULL COMMENT '自动升级条件：团队累计发展有效相亲会员数阈值',
            `register_reward_male` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '男会员注册奖励(元/人)',
            `register_reward_female` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '女会员注册奖励(元/人)',
            `promoter_join_reward` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '推广红娘纳入分成(元/人)',
            `consume_commission_mode` varchar(16) NOT NULL DEFAULT 'none' COMMENT '会员消费分成模式：none 不分成 / auto_rate 按比例',
            `consume_commission_rate` decimal(7,4) DEFAULT NULL COMMENT '会员消费分成比例(%)，consume_commission_mode=auto_rate 时生效',
            `share_bonus` tinyint NOT NULL DEFAULT 1 COMMENT '合伙人同时是自己团队推广红娘时是否享有团队奖励/分成 1享有 0不享有',
            `bonus_items` json DEFAULT NULL COMMENT '按事件的分成金额明细：[{name, amount}]',
            `updated_by` bigint unsigned DEFAULT NULL,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_partner_level` (`level_id`),
            KEY `idx_partner_level_created` (`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='合伙红娘分成级别配置（固定 3 种，不可新增/删除）'
    """,
    "partner_membership": """
        CREATE TABLE IF NOT EXISTS `partner_membership` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `team_id` bigint unsigned NOT NULL,
            `promoter_id` bigint unsigned NOT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1正常 2移出 3变更',
            `joined_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `left_at` datetime DEFAULT NULL,
            `changed_by` bigint unsigned DEFAULT NULL,
            `change_reason` varchar(255) DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_partner_membership_active` (`promoter_id`, `status`),
            KEY `idx_partner_membership_team` (`team_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='合伙团队成员关系'
    """,
    "partner_join_request": """
        CREATE TABLE IF NOT EXISTS `partner_join_request` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `team_id` bigint unsigned NOT NULL,
            `promoter_id` bigint unsigned NOT NULL,
            `invite_code` varchar(64) NOT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED/CANCELLED',
            `reject_reason` varchar(200) DEFAULT NULL,
            `reviewed_by` bigint unsigned DEFAULT NULL,
            `reviewed_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_partner_join_team_status` (`team_id`, `status`),
            KEY `idx_partner_join_promoter` (`promoter_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='推广红娘入团申请'
    """,
    "business_audit_log": """
        CREATE TABLE IF NOT EXISTS `business_audit_log` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `actor_user_id` bigint unsigned DEFAULT NULL,
            `action` varchar(128) NOT NULL,
            `resource_type` varchar(64) NOT NULL,
            `resource_id` bigint unsigned DEFAULT NULL,
            `before_json` json DEFAULT NULL,
            `after_json` json DEFAULT NULL,
            `reason` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_business_audit_resource` (`resource_type`, `resource_id`),
            KEY `idx_business_audit_actor` (`actor_user_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商业化业务审计日志'
    """,
    "matchmaker_service_product": """
        CREATE TABLE IF NOT EXISTS `matchmaker_service_product` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `code` varchar(32) NOT NULL,
            `name` varchar(128) NOT NULL,
            `service_type` tinyint unsigned NOT NULL COMMENT '1付费牵线 3私人定制',
            `price` decimal(12,2) NOT NULL,
            `description` varchar(2000) NOT NULL,
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1上架 2下架',
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_product_code` (`code`),
            KEY `idx_matchmaker_product_status` (`status`, `service_type`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='付费红娘服务商品'
    """,
    "matchmaker_service_contact": """
        CREATE TABLE IF NOT EXISTS `matchmaker_service_contact` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `service_id` bigint unsigned NOT NULL,
            `matchmaker_id` bigint unsigned NOT NULL,
            `wechat_contact` varchar(128) NOT NULL COMMENT '受控展示的红娘微信号',
            `delivered_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_service_contact` (`service_id`),
            KEY `idx_matchmaker_contact_matchmaker` (`matchmaker_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘服务联系方式交付记录'
    """,
    "matchmaker_contact_exchange": """
        CREATE TABLE IF NOT EXISTS `matchmaker_contact_exchange` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `service_id` bigint unsigned NOT NULL,
            `source_user_id` bigint unsigned NOT NULL,
            `target_user_id` bigint unsigned NOT NULL,
            `source_consented_at` datetime DEFAULT NULL,
            `target_consented_at` datetime DEFAULT NULL,
            `status` varchar(32) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/ONE_SIDE_CONSENT/APPROVED/DELIVERED/REVOKED/HIDDEN',
            `delivered_at` datetime DEFAULT NULL,
            `hidden_at` datetime DEFAULT NULL,
            `hidden_reason` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_contact_exchange_service_target` (`service_id`, `source_user_id`, `target_user_id`),
            KEY `idx_matchmaker_contact_exchange_source` (`source_user_id`, `status`),
            KEY `idx_matchmaker_contact_exchange_target` (`target_user_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户双方联系方式授权交换'
    """,
    "matchmaker_service_quota": """
        CREATE TABLE IF NOT EXISTS `matchmaker_service_quota` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `available_count` int unsigned NOT NULL DEFAULT '0',
            `used_count` int unsigned NOT NULL DEFAULT '0',
            `refunded_count` int unsigned NOT NULL DEFAULT '0',
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_quota_user` (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='牵线服务次数账户'
    """,
    "matchmaker_quota_entry": """
        CREATE TABLE IF NOT EXISTS `matchmaker_quota_entry` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `service_id` bigint unsigned NOT NULL,
            `entry_type` varchar(32) NOT NULL COMMENT 'consume/refund',
            `quantity` int unsigned NOT NULL,
            `idempotency_key` varchar(128) NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_quota_entry_key` (`idempotency_key`),
            KEY `idx_matchmaker_quota_entry_service` (`service_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='牵线次数流水'
    """,
    "meeting_request": """
        CREATE TABLE IF NOT EXISTS `meeting_request` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL,
            `target_user_id` bigint unsigned NOT NULL,
            `matchmaker_id` bigint unsigned DEFAULT NULL,
            `service_id` bigint unsigned DEFAULT NULL COMMENT '关联红娘服务单',
            `organization_id` bigint unsigned DEFAULT NULL,
            `status` varchar(32) NOT NULL DEFAULT 'SUBMITTED' COMMENT 'SUBMITTED/CONTACTED/ACCEPTED/DECLINED/CLOSED',
            `note` varchar(2000) NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_meeting_request_user` (`user_id`, `created_at`),
            KEY `idx_meeting_request_target` (`target_user_id`, `status`)
            ,KEY `idx_meeting_request_service` (`service_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='线下约见意向'
    """,
    "meeting_record": """
        CREATE TABLE IF NOT EXISTS `meeting_record` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `request_id` bigint unsigned NOT NULL,
            `organizer_id` bigint unsigned NOT NULL,
            `organization_id` bigint unsigned DEFAULT NULL,
            `scheduled_at` datetime NOT NULL,
            `location` varchar(255) NOT NULL,
            `status` varchar(32) NOT NULL DEFAULT 'SCHEDULED' COMMENT 'SCHEDULED/REMINDED/CHECKED_IN/COMPLETED/CANCELLED/NO_SHOW',
            `cancel_reason` varchar(255) DEFAULT NULL,
            `member_visible` tinyint NOT NULL DEFAULT 1 COMMENT '会员端是否可见 1是 0隐藏',
            `sms_remind` tinyint NOT NULL DEFAULT 1 COMMENT '是否发送约会短信提醒 1是 0否',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_meeting_record_request` (`request_id`),
            KEY `idx_meeting_record_time` (`scheduled_at`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='线下约会记录'
    """,
    "meeting_feedback": """
        CREATE TABLE IF NOT EXISTS `meeting_feedback` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `meeting_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL,
            `target_rating` tinyint unsigned DEFAULT NULL,
            `matchmaker_rating` tinyint unsigned DEFAULT NULL,
            `continue_intent` tinyint DEFAULT NULL COMMENT '1愿意 2不确定 3不愿意',
            `private_feedback` varchar(2000) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_meeting_feedback_user` (`meeting_id`, `user_id`),
            KEY `idx_meeting_feedback_meeting` (`meeting_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='约会反馈'
    """,
    "commission_rule": """
        CREATE TABLE IF NOT EXISTS `commission_rule` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `beneficiary_type` varchar(32) NOT NULL COMMENT 'service_matchmaker/store/promoter/partner',
            `name` varchar(128) NOT NULL,
            `mode` varchar(16) NOT NULL COMMENT 'fixed/rate',
            `fixed_amount` decimal(12,2) DEFAULT NULL,
            `rate_percent` decimal(7,4) DEFAULT NULL,
            `priority` int NOT NULL DEFAULT '0',
            `status` tinyint NOT NULL DEFAULT '1',
            `version` int unsigned NOT NULL DEFAULT '1',
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_commission_rule_scope` (`beneficiary_type`, `status`, `priority`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分成规则版本'
    """,
    "product_commission_config": """
        CREATE TABLE IF NOT EXISTS `product_commission_config` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `product_id` bigint unsigned NOT NULL,
            `beneficiary_type` varchar(32) NOT NULL,
            `mode` varchar(16) NOT NULL COMMENT 'fixed/rate',
            `fixed_amount` decimal(12,2) DEFAULT NULL,
            `rate_percent` decimal(7,4) DEFAULT NULL,
            `version` int unsigned NOT NULL DEFAULT '1',
            `status` tinyint NOT NULL DEFAULT '1' COMMENT '1生效 2停用',
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_product_commission_active` (`product_id`, `beneficiary_type`, `status`),
            KEY `idx_product_commission_product` (`product_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品分成对象配置'
    """,
    "commission_entry": """
        CREATE TABLE IF NOT EXISTS `commission_entry` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `order_id` bigint unsigned DEFAULT NULL COMMENT '关联订单；后台手工录入时为空',
            `beneficiary_type` varchar(32) NOT NULL,
            `beneficiary_id` bigint unsigned NOT NULL,
            `rule_id` bigint unsigned DEFAULT NULL,
            `rule_version` int unsigned DEFAULT NULL,
            `base_amount` decimal(12,2) NOT NULL,
            `amount` decimal(12,2) NOT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/AVAILABLE/FROZEN/REVERSED',
            `source` varchar(16) NOT NULL DEFAULT 'order' COMMENT 'order 订单产生 / manual 后台手工录入',
            `remark` varchar(255) DEFAULT NULL COMMENT '后台手工录入备注',
            `idempotency_key` varchar(160) NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_commission_entry_key` (`idempotency_key`),
            KEY `idx_commission_entry_beneficiary` (`beneficiary_type`, `beneficiary_id`, `status`),
            KEY `idx_commission_entry_order` (`order_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单分成明细'
    """,
    "account_ledger": """
        CREATE TABLE IF NOT EXISTS `account_ledger` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_type` varchar(32) NOT NULL COMMENT 'user/store/platform',
            `account_id` bigint unsigned NOT NULL,
            `direction` varchar(8) NOT NULL COMMENT 'CREDIT/DEBIT',
            `amount` decimal(12,2) NOT NULL,
            `state` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/AVAILABLE/REVERSED',
            `source_type` varchar(32) NOT NULL,
            `source_id` bigint unsigned NOT NULL,
            `idempotency_key` varchar(160) NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_account_ledger_key` (`idempotency_key`),
            KEY `idx_account_ledger_account` (`account_type`, `account_id`, `state`),
            KEY `idx_account_ledger_source` (`source_type`, `source_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='不可变资金账本'
    """,
    "withdrawal_request": """
        CREATE TABLE IF NOT EXISTS `withdrawal_request` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `account_type` varchar(32) NOT NULL,
            `account_id` bigint unsigned NOT NULL,
            `amount` decimal(12,2) NOT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'PENDING_REVIEW' COMMENT 'PENDING_REVIEW/APPROVED/REJECTED/PROCESSING/SUCCEEDED/FAILED',
            `payee_masked` varchar(128) DEFAULT NULL,
            `reviewed_by` bigint unsigned DEFAULT NULL,
            `reviewed_at` datetime DEFAULT NULL,
            `failure_reason` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_withdrawal_account` (`account_type`, `account_id`, `status`),
            KEY `idx_withdrawal_status` (`status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提现申请'
    """,
    "withdrawal_event": """
        CREATE TABLE IF NOT EXISTS `withdrawal_event` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `withdrawal_id` bigint unsigned NOT NULL,
            `event_type` varchar(32) NOT NULL,
            `provider_event_id` varchar(128) DEFAULT NULL,
            `payload_hash` char(64) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_withdrawal_provider_event` (`provider_event_id`),
            KEY `idx_withdrawal_event_withdrawal` (`withdrawal_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='提现支付事件'
    """,
    "chat_session_request": """
        CREATE TABLE IF NOT EXISTS `chat_session_request` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `session_id` bigint unsigned NOT NULL,
            `requester_id` bigint unsigned NOT NULL,
            `responder_id` bigint unsigned NOT NULL,
            `request_type` varchar(32) NOT NULL,
            `payload` json DEFAULT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'PENDING',
            `expire_at` datetime DEFAULT NULL,
            `responded_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_chat_request_session` (`session_id`,`status`,`created_at`),
            KEY `idx_chat_request_responder` (`responder_id`,`status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会话结构化请求'
    """,
    "commission_level": """
        CREATE TABLE IF NOT EXISTS `commission_level` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `code` varchar(32) NOT NULL,
            `name` varchar(64) NOT NULL,
            `mode` varchar(16) NOT NULL DEFAULT 'rate' COMMENT 'rate按比例/fixed固定金额',
            `rate_percent` decimal(7,4) NOT NULL DEFAULT 0 COMMENT '按比例分成比例(%)',
            `fixed_amount` decimal(12,2) DEFAULT NULL COMMENT '固定分成金额(元)，mode=fixed 时生效',
            `platform_extra_amount` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '平台额外奖励(元)，每达成一次分成订单额外发放',
            `platform_extra_pay_mode` varchar(16) NOT NULL DEFAULT 'manual' COMMENT '平台额外奖励支付方式 manual人工转账/balance转入余额',
            `promotion_condition` varchar(255) DEFAULT NULL COMMENT '自动升级到此级别的条件描述',
            `sort` int NOT NULL DEFAULT 0,
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 2停用',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_commission_level_code` (`code`),
            KEY `idx_commission_level_status` (`status`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘分成级别'
    """,
    "promoter_level_config": """
        CREATE TABLE IF NOT EXISTS `promoter_level_config` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `level_id` tinyint unsigned NOT NULL COMMENT '业务级别：1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使（固定 4 种）',
            `level_name` varchar(32) NOT NULL COMMENT '级别名称',
            `auto_split_mode` varchar(16) NOT NULL DEFAULT 'fixed_amount' COMMENT 'fixed_amount 自定义固定金额 / auto_rate 按同比自动计算',
            `auto_split_rate` decimal(7,4) DEFAULT NULL COMMENT '按同比自动计算的比例(%)，auto_split_mode=auto_rate 时生效',
            `promote_threshold` int DEFAULT NULL COMMENT '自动升级条件：累计发展有效相亲会员数阈值',
            `register_reward_male` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '男会员注册奖励(元/人)',
            `register_reward_female` decimal(12,2) NOT NULL DEFAULT 0 COMMENT '女会员注册奖励(元/人)',
            `consume_commission_mode` varchar(16) NOT NULL DEFAULT 'none' COMMENT '会员消费分成模式：none 不分成 / auto_rate 按比例',
            `consume_commission_rate` decimal(7,4) DEFAULT NULL COMMENT '会员消费分成比例(%)，consume_commission_mode=auto_rate 时生效',
            `updated_by` bigint unsigned DEFAULT NULL,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_promoter_level` (`level_id`),
            KEY `idx_promoter_level_created` (`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='推广红娘分成级别配置（固定 4 种，不可新增/删除）'
    """,
    "matchmaker_profile": """
        CREATE TABLE IF NOT EXISTS `matchmaker_profile` (
            `user_id` bigint unsigned NOT NULL,
            `wechat` varchar(64) DEFAULT NULL,
            `commission_level_id` bigint unsigned DEFAULT NULL,
            `role_tag` varchar(16) NOT NULL DEFAULT 'normal' COMMENT 'super超级红娘 normal普通红娘',
            `visible` tinyint NOT NULL DEFAULT 1 COMMENT '1前台展示 0隐藏',
            `locked` tinyint NOT NULL DEFAULT 0 COMMENT '1锁定禁止登录工作台',
            `description` varchar(2000) DEFAULT NULL,
            `slogan` varchar(64) DEFAULT NULL COMMENT '红娘口号',
            `sort` int NOT NULL DEFAULT 0 COMMENT '显示排序，数字越大越靠前',
            `contact_editable` tinyint NOT NULL DEFAULT 1 COMMENT '是否允许红娘在其工作台修改客户手机号/微信 1允许 0不允许',
            `lock_at` datetime DEFAULT NULL COMMENT '定时锁定时间，到点自动锁定账号',
            `wechat_qr` varchar(500) DEFAULT NULL COMMENT '红娘微信二维码图片地址',
            `deleted_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`user_id`),
            KEY `idx_matchmaker_profile_level` (`commission_level_id`, `visible`),
            KEY `idx_matchmaker_profile_lock` (`locked`, `deleted_at`),
            KEY `idx_matchmaker_profile_sort` (`sort`, `deleted_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘管理档案'
    """,
    "admin_menu": """
        CREATE TABLE IF NOT EXISTS `admin_menu` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `parent_id` bigint unsigned DEFAULT NULL,
            `name` varchar(64) NOT NULL,
            `path` varchar(128) DEFAULT NULL,
            `menu_type` varchar(16) NOT NULL DEFAULT 'menu' COMMENT 'directory/menu/button',
            `permission_code` varchar(64) DEFAULT NULL,
            `icon` varchar(64) DEFAULT NULL,
            `sort` int NOT NULL DEFAULT 0,
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用 2停用',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_admin_menu_parent` (`parent_id`, `status`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘后台菜单权限树'
    """,
    "matchmaker_menu_permission": """
        CREATE TABLE IF NOT EXISTS `matchmaker_menu_permission` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `matchmaker_user_id` bigint unsigned NOT NULL,
            `menu_id` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_matchmaker_menu` (`matchmaker_user_id`, `menu_id`),
            KEY `idx_matchmaker_menu_user` (`matchmaker_user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘已分配菜单'
    """,
    "matchmaker_tutorial": """
        CREATE TABLE IF NOT EXISTS `matchmaker_tutorial` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `title` varchar(128) NOT NULL,
            `content` text NOT NULL,
            `link_url` varchar(500) DEFAULT NULL,
            `status` tinyint NOT NULL DEFAULT 1,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘使用教程'
    """,
    # ============================================
    # 运营工具/活动/商家/短视频等通用内容项（2026-09 对标补齐）
    # ============================================
    "admin_content_item": """
        CREATE TABLE IF NOT EXISTS `admin_content_item` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `tenant_id` bigint unsigned NOT NULL DEFAULT 1,
            `domain` varchar(64) NOT NULL,
            `title` varchar(255) NOT NULL DEFAULT '',
            `subtitle` varchar(500) DEFAULT NULL,
            `image_url` varchar(500) DEFAULT NULL,
            `amount` decimal(12,2) DEFAULT NULL,
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1正常 2停用/隐藏',
            `sort` int NOT NULL DEFAULT 0,
            `extra_json` longtext NOT NULL,
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_content_domain` (`tenant_id`, `domain`, `status`, `sort`),
            KEY `idx_content_created` (`domain`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='活动/商家/短视频/礼品等通用内容项'
    """,
    "live_session": """
        CREATE TABLE IF NOT EXISTS `live_session` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `title` varchar(128) NOT NULL,
            `city_code` varchar(32) DEFAULT NULL,
            `scheduled_at` datetime NOT NULL,
            `status` varchar(32) NOT NULL DEFAULT 'DRAFT',
            `state_version` int unsigned NOT NULL DEFAULT 1,
            `host_user_id` bigint unsigned NOT NULL,
            `max_stage_seats` tinyint unsigned NOT NULL DEFAULT 8,
            `recording_enabled` tinyint NOT NULL DEFAULT 0,
            `rules_text` text DEFAULT NULL,
            `created_by` bigint unsigned NOT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `closed_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`), KEY `idx_live_session_status_time` (`status`, `scheduled_at`),
            KEY `idx_live_session_host` (`host_user_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播相亲场次'
    """,
    "live_session_role": """
        CREATE TABLE IF NOT EXISTS `live_session_role` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `session_id` bigint unsigned NOT NULL, `user_id` bigint unsigned NOT NULL,
            `role_code` varchar(24) NOT NULL, `status` tinyint NOT NULL DEFAULT 1,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_live_role` (`session_id`, `user_id`, `role_code`),
            KEY `idx_live_role_user` (`user_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播场次角色'
    """,
    "live_registration": """
        CREATE TABLE IF NOT EXISTS `live_registration` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `session_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL, `status` varchar(24) NOT NULL DEFAULT 'RESERVED',
            `device_check_passed` tinyint NOT NULL DEFAULT 0, `checked_in_at` datetime DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_live_registration` (`session_id`, `user_id`),
            KEY `idx_live_registration_status` (`session_id`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播预约签到'
    """,
    "live_stage_seat": """
        CREATE TABLE IF NOT EXISTS `live_stage_seat` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `session_id` bigint unsigned NOT NULL,
            `seat_no` tinyint unsigned NOT NULL, `user_id` bigint unsigned DEFAULT NULL,
            `status` varchar(24) NOT NULL DEFAULT 'EMPTY', `invited_by` bigint unsigned DEFAULT NULL,
            `invitation_token` char(32) DEFAULT NULL, `invitation_expires_at` datetime DEFAULT NULL,
            `joined_at` datetime DEFAULT NULL, `left_at` datetime DEFAULT NULL,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_live_seat_no` (`session_id`, `seat_no`),
            UNIQUE KEY `uk_live_invitation_token` (`invitation_token`), KEY `idx_live_seat_user` (`session_id`, `user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播舞台席位'
    """,
    "live_state_transition": """
        CREATE TABLE IF NOT EXISTS `live_state_transition` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `session_id` bigint unsigned NOT NULL,
            `from_status` varchar(32) NOT NULL, `to_status` varchar(32) NOT NULL,
            `state_version` int unsigned NOT NULL, `actor_user_id` bigint unsigned NOT NULL,
            `reason` varchar(255) DEFAULT NULL, `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_live_transition_version` (`session_id`, `state_version`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播状态迁移'
    """,
    "live_interaction": """
        CREATE TABLE IF NOT EXISTS `live_interaction` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `session_id` bigint unsigned NOT NULL,
            `actor_user_id` bigint unsigned NOT NULL, `target_user_id` bigint unsigned NOT NULL,
            `interaction_type` varchar(24) NOT NULL, `status` varchar(24) NOT NULL DEFAULT 'ACTIVE',
            `idempotency_key` varchar(128) NOT NULL, `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), UNIQUE KEY `uk_live_interaction_key` (`actor_user_id`, `idempotency_key`),
            KEY `idx_live_interaction_pair` (`session_id`, `interaction_type`, `actor_user_id`, `target_user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播亮灯选择确认'
    """,
    "live_report": """
        CREATE TABLE IF NOT EXISTS `live_report` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT, `session_id` bigint unsigned NOT NULL,
            `reporter_user_id` bigint unsigned NOT NULL, `target_user_id` bigint unsigned NOT NULL,
            `category` varchar(32) NOT NULL, `description` varchar(500) DEFAULT NULL,
            `status` varchar(24) NOT NULL DEFAULT 'PENDING', `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`), KEY `idx_live_report_session` (`session_id`, `status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播举报'
    """,
    "live_provider_event": """
        CREATE TABLE IF NOT EXISTS `live_provider_event` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `provider` varchar(24) NOT NULL,
            `provider_event_id` varchar(128) NOT NULL,
            `event_type` varchar(64) DEFAULT NULL,
            `session_id` bigint unsigned DEFAULT NULL,
            `payload_hash` char(64) NOT NULL,
            `status` varchar(24) NOT NULL DEFAULT 'RECEIVED',
            `received_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `processed_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_live_provider_event` (`provider`, `provider_event_id`),
            KEY `idx_live_provider_session` (`session_id`, `received_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播云厂商回调事件'
    """,
    "live_moderation_action": """
        CREATE TABLE IF NOT EXISTS `live_moderation_action` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `session_id` bigint unsigned NOT NULL,
            `actor_user_id` bigint unsigned NOT NULL,
            `target_user_id` bigint unsigned NOT NULL,
            `action_type` varchar(32) NOT NULL,
            `reason` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_live_moderation_session` (`session_id`, `created_at`),
            KEY `idx_live_moderation_target` (`target_user_id`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='直播人工处置记录'
    """,
    # ── M7-A 互选活动 ──────────────────────────────────────────────
    "mutual_selection_activity": """
        CREATE TABLE IF NOT EXISTS `mutual_selection_activity` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `title` varchar(80) NOT NULL COMMENT '活动标题',
            `cover` varchar(255) DEFAULT NULL COMMENT '封面图',
            `start_time` datetime NOT NULL COMMENT '活动开始时间',
            `end_time` datetime NOT NULL COMMENT '活动结束时间',
            `pick_limit` int NOT NULL DEFAULT 5 COMMENT '每人可选心动嘉宾次数',
            `virtual_signup` int NOT NULL DEFAULT 0 COMMENT '显示报名人数基数',
            `price_male` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '男生费用',
            `price_female` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '女生费用',
            `price_vip` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT 'VIP会员费用',
            `reward_promoter` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '推广红娘奖励',
            `reward_service` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '服务红娘奖励',
            `require_realname` tinyint NOT NULL DEFAULT 0 COMMENT '报名要求-实名认证',
            `require_avatar` tinyint NOT NULL DEFAULT 0 COMMENT '报名要求-必须有头像',
            `require_three_photo` tinyint NOT NULL DEFAULT 0 COMMENT '报名要求-至少3张照片',
            `intro` text COMMENT '活动介绍',
            `share_title` varchar(80) DEFAULT NULL COMMENT '分享标题',
            `share_desc` varchar(500) DEFAULT NULL COMMENT '分享描述',
            `share_icon` varchar(255) DEFAULT NULL COMMENT '分享图标',
            `success_mode` varchar(24) NOT NULL DEFAULT 'show_wechat' COMMENT 'show_wechat 显示双方微信 / contact_matchmaker 联系红娘推送',
            `notice_html` text COMMENT '进入嘉宾互选时弹出的须知',
            `success_notice` text COMMENT '互选成功后添加微信页面的提示',
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1报名中 2进行中 3已结束 4已取消',
            `visible` tinyint NOT NULL DEFAULT 1 COMMENT '是否上线 1是 0否',
            `sort` int NOT NULL DEFAULT 0 COMMENT '显示排序',
            `created_by` bigint unsigned DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_msa_status` (`status`, `start_time`),
            KEY `idx_msa_visible` (`visible`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选活动'
    """,
    "mutual_selection_signup": """
        CREATE TABLE IF NOT EXISTS `mutual_selection_signup` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `activity_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL,
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1已参与 2已退出',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_ms_signup` (`activity_id`, `user_id`),
            KEY `idx_ms_signup_user` (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选活动参与嘉宾'
    """,
    "mutual_selection_pick": """
        CREATE TABLE IF NOT EXISTS `mutual_selection_pick` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `activity_id` bigint unsigned NOT NULL,
            `from_user_id` bigint unsigned NOT NULL COMMENT '行为方',
            `to_user_id` bigint unsigned NOT NULL COMMENT '行为对象',
            `action` varchar(16) NOT NULL DEFAULT 'pick' COMMENT 'pick 选择心动 / cancel 取消心动',
            `is_success` tinyint NOT NULL DEFAULT 0 COMMENT '是否互选成功',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_ms_pick` (`activity_id`, `from_user_id`, `to_user_id`),
            KEY `idx_ms_pick_activity` (`activity_id`, `created_at`),
            KEY `idx_ms_pick_from` (`from_user_id`),
            KEY `idx_ms_pick_to` (`to_user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选活动选择记录'
    """,
    # ── M7-B 商家联盟 ──────────────────────────────────────────────
    "merchant_category": """
        CREATE TABLE IF NOT EXISTS `merchant_category` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `name` varchar(64) NOT NULL COMMENT '分类名称',
            `icon_url` varchar(255) DEFAULT NULL,
            `sort` int NOT NULL DEFAULT 0,
            `status` tinyint NOT NULL DEFAULT 1,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_merchant_category_name` (`name`),
            KEY `idx_merchant_category_sort` (`status`, `sort`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家分类'
    """,
    "merchant": """
        CREATE TABLE IF NOT EXISTS `merchant` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `name` varchar(128) NOT NULL COMMENT '商家名称',
            `cover` varchar(255) DEFAULT NULL COMMENT '商家封面',
            `gallery_json` text COMMENT '商家相册 JSON 数组',
            `category_id` bigint unsigned DEFAULT NULL COMMENT '商家分类',
            `tags` varchar(255) DEFAULT NULL COMMENT '特色标签，逗号分隔',
            `province` varchar(64) DEFAULT NULL,
            `city` varchar(64) DEFAULT NULL,
            `address` varchar(255) DEFAULT NULL,
            `contact_phone` varchar(32) DEFAULT NULL,
            `business_hours` varchar(128) DEFAULT NULL,
            `intro` text COMMENT '商家介绍',
            `admin_user_id` bigint unsigned DEFAULT NULL COMMENT '管理账号（推广红娘 users.id）',
            `sort` int NOT NULL DEFAULT 0 COMMENT '显示排序',
            `visible` tinyint NOT NULL DEFAULT 1 COMMENT '展示 1是 0否',
            `link_url` varchar(255) DEFAULT NULL,
            `qr_code` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `deleted_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `idx_merchant_category` (`category_id`),
            KEY `idx_merchant_admin` (`admin_user_id`),
            KEY `idx_merchant_visible` (`visible`, `sort`),
            KEY `idx_merchant_deleted` (`deleted_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='联盟商家'
    """,
    "merchant_product": """
        CREATE TABLE IF NOT EXISTS `merchant_product` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `merchant_id` bigint unsigned NOT NULL COMMENT '合作商家',
            `name` varchar(128) NOT NULL COMMENT '商品名称',
            `cover` varchar(255) DEFAULT NULL COMMENT '商品封面',
            `original_price` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '原价值',
            `sale_price` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '合作优惠价',
            `settle_amount` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '商家结算 元/份',
            `promote_split_mode` varchar(16) NOT NULL DEFAULT 'fixed' COMMENT 'fixed 统一设置 / by_level 按级别',
            `promote_amount` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '推广红娘分成 元/份',
            `partner_split_mode` varchar(16) NOT NULL DEFAULT 'fixed',
            `partner_amount` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '合伙红娘分成 元/份',
            `service_amount` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '服务红娘分成 元/份',
            `buy_limit_mode` varchar(16) NOT NULL DEFAULT 'account' COMMENT 'account 每账号 / order 每订单',
            `account_limit` int NOT NULL DEFAULT 0 COMMENT '每账号限购份数',
            `order_limit` int NOT NULL DEFAULT 0 COMMENT '每订单限购份数',
            `notice_mode` varchar(16) NOT NULL DEFAULT 'default' COMMENT 'default 使用默认 / custom 自定义',
            `notice_text` text COMMENT '自定义购买须知',
            `intro` text COMMENT '商品介绍',
            `status` tinyint NOT NULL DEFAULT 1 COMMENT '1上架 2下架',
            `link_url` varchar(255) DEFAULT NULL,
            `qr_code` varchar(255) DEFAULT NULL,
            `sort` int NOT NULL DEFAULT 0,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `deleted_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `idx_merchant_product_merchant` (`merchant_id`),
            KEY `idx_merchant_product_status` (`status`, `sort`),
            KEY `idx_merchant_product_deleted` (`deleted_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='联盟商家商品'
    """,
    "merchant_order": """
        CREATE TABLE IF NOT EXISTS `merchant_order` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `order_no` varchar(64) NOT NULL COMMENT '订单号',
            `product_id` bigint unsigned NOT NULL,
            `merchant_id` bigint unsigned NOT NULL,
            `buyer_user_id` bigint unsigned NOT NULL COMMENT '下单人',
            `quantity` int NOT NULL DEFAULT 1,
            `amount` decimal(12,2) NOT NULL DEFAULT 0.00 COMMENT '订单金额',
            `pay_status` varchar(16) NOT NULL DEFAULT 'unpaid' COMMENT 'unpaid/paid/refunded',
            `pay_method` varchar(32) DEFAULT NULL COMMENT '余额支付/微信支付等',
            `paid_at` datetime DEFAULT NULL,
            `verify_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending 未核销 / verified 已核销',
            `verify_code` varchar(16) DEFAULT NULL COMMENT '5位消费码',
            `verified_at` datetime DEFAULT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending 未支付 / paid 已支付 / used 已消费 / cancelled 已取消',
            `remark` varchar(255) DEFAULT NULL,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_merchant_order_no` (`order_no`),
            KEY `idx_merchant_order_product` (`product_id`),
            KEY `idx_merchant_order_merchant` (`merchant_id`, `created_at`),
            KEY `idx_merchant_order_buyer` (`buyer_user_id`),
            KEY `idx_merchant_order_status` (`status`, `verify_status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='联盟商家订单'
    """,
    # ── M7-C 短视频 ────────────────────────────────────────────────
    "short_video_category": """
        CREATE TABLE IF NOT EXISTS `short_video_category` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `name` varchar(64) NOT NULL,
            `sort` int NOT NULL DEFAULT 0,
            `status` tinyint NOT NULL DEFAULT 1,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_short_video_category_name` (`name`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频分类'
    """,
    "short_video": """
        CREATE TABLE IF NOT EXISTS `short_video` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `publisher_user_id` bigint unsigned NOT NULL COMMENT '发布账号 users.id',
            `cover` varchar(255) DEFAULT NULL,
            `cover_mode` varchar(16) NOT NULL DEFAULT 'auto' COMMENT 'auto 系统自动截图 / custom 自定义上传',
            `description` varchar(255) DEFAULT NULL COMMENT '视频描述',
            `category_id` bigint unsigned DEFAULT NULL,
            `duration_seconds` decimal(8,2) NOT NULL DEFAULT 0.00,
            `video_url` varchar(500) DEFAULT NULL,
            `link_type` varchar(24) NOT NULL DEFAULT 'none' COMMENT 'none/custom/member/activity/home',
            `link_value` varchar(255) DEFAULT NULL,
            `view_permission` varchar(16) NOT NULL DEFAULT 'login' COMMENT 'login 必须先登录 / all 不限 / member 仅会员',
            `sort` int NOT NULL DEFAULT 0 COMMENT '显示排序',
            `virtual_views` int NOT NULL DEFAULT 0 COMMENT '虚拟播放基数',
            `comment_enabled` tinyint NOT NULL DEFAULT 1,
            `tip_enabled` tinyint NOT NULL DEFAULT 1,
            `published_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `visible` tinyint NOT NULL DEFAULT 1,
            `audit_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/rejected',
            `is_top` tinyint NOT NULL DEFAULT 0,
            `is_recommend` tinyint NOT NULL DEFAULT 0,
            `is_hot` tinyint NOT NULL DEFAULT 0,
            `has_red_packet` tinyint NOT NULL DEFAULT 0,
            `view_count` int NOT NULL DEFAULT 0,
            `comment_count` int NOT NULL DEFAULT 0,
            `like_count` int NOT NULL DEFAULT 0,
            `tip_amount` decimal(12,2) NOT NULL DEFAULT 0.00,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `deleted_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            KEY `idx_short_video_publisher` (`publisher_user_id`),
            KEY `idx_short_video_category` (`category_id`),
            KEY `idx_short_video_audit` (`audit_status`, `published_at`),
            KEY `idx_short_video_deleted` (`deleted_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频'
    """,
    "short_video_comment": """
        CREATE TABLE IF NOT EXISTS `short_video_comment` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `video_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL,
            `content` varchar(500) NOT NULL,
            `like_count` int NOT NULL DEFAULT 0,
            `ip` varchar(64) DEFAULT NULL,
            `audit_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/rejected',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_svc_video` (`video_id`, `created_at`),
            KEY `idx_svc_audit` (`audit_status`, `created_at`),
            KEY `idx_svc_user` (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频评论'
    """,
    "short_video_tip": """
        CREATE TABLE IF NOT EXISTS `short_video_tip` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `video_id` bigint unsigned NOT NULL,
            `tipper_user_id` bigint unsigned NOT NULL COMMENT '打赏用户',
            `receiver_user_id` bigint unsigned NOT NULL COMMENT '受赏用户',
            `message` varchar(200) DEFAULT NULL COMMENT '打赏附言',
            `tip_form` varchar(32) NOT NULL DEFAULT 'cash' COMMENT 'cash 现金 / gift 礼物',
            `amount` decimal(12,2) NOT NULL DEFAULT 0.00,
            `pay_method` varchar(32) DEFAULT NULL,
            `order_no` varchar(64) DEFAULT NULL,
            `status` varchar(16) NOT NULL DEFAULT 'paid' COMMENT 'unpaid/paid/refunded',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_svt_video` (`video_id`),
            KEY `idx_svt_tipper` (`tipper_user_id`, `created_at`),
            KEY `idx_svt_receiver` (`receiver_user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频打赏'
    """,
    "video_red_packet": """
        CREATE TABLE IF NOT EXISTS `video_red_packet` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `video_id` bigint unsigned NOT NULL,
            `sender_user_id` bigint unsigned DEFAULT NULL COMMENT '发红包用户，NULL 表示后台发放',
            `sender_label` varchar(32) NOT NULL DEFAULT '后台发放',
            `amount` decimal(12,2) NOT NULL DEFAULT 0.00,
            `total_parts` int NOT NULL DEFAULT 1,
            `is_equal` tinyint NOT NULL DEFAULT 0 COMMENT '是否均分',
            `remain_parts` int NOT NULL DEFAULT 0,
            `remain_amount` decimal(12,2) NOT NULL DEFAULT 0.00,
            `pay_status` varchar(16) NOT NULL DEFAULT 'unpaid' COMMENT 'unpaid/paid/refunded',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_vrp_video` (`video_id`),
            KEY `idx_vrp_pay` (`pay_status`, `created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频红包'
    """,
    "video_red_packet_claim": """
        CREATE TABLE IF NOT EXISTS `video_red_packet_claim` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `packet_id` bigint unsigned NOT NULL,
            `user_id` bigint unsigned NOT NULL,
            `amount` decimal(12,2) NOT NULL DEFAULT 0.00,
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`),
            KEY `idx_vrpc_packet` (`packet_id`, `created_at`),
            KEY `idx_vrpc_user` (`user_id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频红包领取明细'
    """,
    "short_video_homepage": """
        CREATE TABLE IF NOT EXISTS `short_video_homepage` (
            `id` bigint unsigned NOT NULL AUTO_INCREMENT,
            `user_id` bigint unsigned NOT NULL COMMENT '会员 ID',
            `wechat` varchar(64) DEFAULT NULL COMMENT '微信号',
            `bio` varchar(255) DEFAULT NULL COMMENT '主页简介',
            `follower_count` int NOT NULL DEFAULT 0 COMMENT '粉丝量',
            `certified` tinyint NOT NULL DEFAULT 0 COMMENT '认证 1是 0否',
            `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            `deleted_at` datetime DEFAULT NULL,
            PRIMARY KEY (`id`),
            UNIQUE KEY `uk_video_homepage_user` (`user_id`),
            KEY `idx_video_homepage_deleted` (`deleted_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频会员主页'
    """,
}
