-- M4–M7 后台运营补列迁移（up）。
-- 逐条镜像 database_setup_marriage.py:3384-3502 的 _ensure_m4_columns / _ensure_m5_columns /
-- _ensure_m6_columns / _ensure_m7_columns（含三个索引与 partner_level_config /
-- merchant_category / short_video_category 种子），用于 AUTO_INIT_DB=false 的生产/预发布库
-- 补齐提交 8ec8ff1 事故中从未创建成功的后台运营列。详见 migrations/m4_m7/README.md。
-- 幂等约定与 migrations/chat/20260923_01 相同：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE
-- 动态 DDL，可重复执行；表不存在时补列与索引跳过（与 _ensure_table_columns /
-- _ensure_optional_index 的跳过语义一致）。
-- Run with the intended database selected (MySQL 8+).

-- offline_activity 27 列拼接后的 ALTER 远超 GROUP_CONCAT 默认 1024 字节上限，必须放开。
SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ M4 客源线索 / 会员服务补充字段 ============
SET @m4_customer_lead_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'promoter_id' AS column_name,
               '`promoter_id` bigint unsigned DEFAULT NULL COMMENT ''推广红娘用户ID''' AS column_ddl
        UNION ALL
        SELECT 2, 'audit_status',
               '`audit_status` varchar(16) NOT NULL DEFAULT ''active'' COMMENT ''active有效/pending待核'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m4_customer_lead_ddl = IF(
    @m4_customer_lead_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @m4_customer_lead_missing)
);
PREPARE m4_customer_lead_cols_stmt FROM @m4_customer_lead_ddl;
EXECUTE m4_customer_lead_cols_stmt;
DEALLOCATE PREPARE m4_customer_lead_cols_stmt;

SET @m4_meeting_record_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'member_visible' AS column_name,
               '`member_visible` tinyint NOT NULL DEFAULT 1 COMMENT ''会员端是否可见 1是 0隐藏''' AS column_ddl
        UNION ALL
        SELECT 2, 'sms_remind',
               '`sms_remind` tinyint NOT NULL DEFAULT 1 COMMENT ''是否发送约会短信提醒 1是 0否'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'meeting_record'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'meeting_record' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m4_meeting_record_ddl = IF(
    @m4_meeting_record_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `meeting_record` ', @m4_meeting_record_missing)
);
PREPARE m4_meeting_record_cols_stmt FROM @m4_meeting_record_ddl;
EXECUTE m4_meeting_record_cols_stmt;
DEALLOCATE PREPARE m4_meeting_record_cols_stmt;

-- 幂等创建索引 idx_customer_lead_promoter（表不存在或索引已存在时跳过）。
SET @m4_customer_lead_table_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead'
);
SET @m4_customer_lead_promoter_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead'
      AND INDEX_NAME = 'idx_customer_lead_promoter'
);
SET @m4_customer_lead_index_ddl = IF(
    @m4_customer_lead_table_exists > 0 AND @m4_customer_lead_promoter_index_exists = 0,
    'ALTER TABLE `customer_lead` ADD KEY `idx_customer_lead_promoter` (`promoter_id`)',
    'SELECT 1'
);
PREPARE m4_customer_lead_promoter_idx_stmt FROM @m4_customer_lead_index_ddl;
EXECUTE m4_customer_lead_promoter_idx_stmt;
DEALLOCATE PREPARE m4_customer_lead_promoter_idx_stmt;

-- ============ M5 分店管理补充字段 ============
SET @m5_organization_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'link_url' AS column_name,
               '`link_url` varchar(255) DEFAULT NULL COMMENT ''分站访问链接''' AS column_ddl
        UNION ALL
        SELECT 2, 'sort_order',
               '`sort_order` int NOT NULL DEFAULT 0 COMMENT ''显示排序，数字越大越靠前'''
        UNION ALL
        SELECT 3, 'qr_code',
               '`qr_code` varchar(500) DEFAULT NULL COMMENT ''分站链接/二维码图片地址'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'organization'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'organization' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m5_organization_ddl = IF(
    @m5_organization_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `organization` ', @m5_organization_missing)
);
PREPARE m5_organization_cols_stmt FROM @m5_organization_ddl;
EXECUTE m5_organization_cols_stmt;
DEALLOCATE PREPARE m5_organization_cols_stmt;

-- ============ M6 合伙红娘补充字段与种子 ============
SET @m6_partner_team_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'level_id' AS column_name,
               '`level_id` tinyint unsigned NOT NULL DEFAULT 1 COMMENT ''合伙级别：1 初级 / 2 中级 / 3 战略合伙人（固定 3 种）''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'partner_team'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'partner_team' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m6_partner_team_ddl = IF(
    @m6_partner_team_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `partner_team` ', @m6_partner_team_missing)
);
PREPARE m6_partner_team_cols_stmt FROM @m6_partner_team_ddl;
EXECUTE m6_partner_team_cols_stmt;
DEALLOCATE PREPARE m6_partner_team_cols_stmt;

SET @m6_commission_entry_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'source' AS column_name,
               '`source` varchar(16) NOT NULL DEFAULT ''order'' COMMENT ''order 订单产生 / manual 后台手工录入''' AS column_ddl
        UNION ALL
        SELECT 2, 'remark',
               '`remark` varchar(255) DEFAULT NULL COMMENT ''后台手工录入备注'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'commission_entry'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'commission_entry' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m6_commission_entry_ddl = IF(
    @m6_commission_entry_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `commission_entry` ', @m6_commission_entry_missing)
);
PREPARE m6_commission_entry_cols_stmt FROM @m6_commission_entry_ddl;
EXECUTE m6_commission_entry_cols_stmt;
DEALLOCATE PREPARE m6_commission_entry_cols_stmt;

-- commission_entry.order_id 放开为可空（支持后台手工录入分成；当前已是可空时跳过）。
SET @m6_commission_entry_order_id_nullable = (
    SELECT IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'commission_entry' AND COLUMN_NAME = 'order_id'
    LIMIT 1
);
SET @m6_commission_entry_order_id_ddl = IF(
    @m6_commission_entry_order_id_nullable = 'NO',
    'ALTER TABLE `commission_entry` MODIFY COLUMN `order_id` bigint unsigned DEFAULT NULL',
    'SELECT 1'
);
PREPARE m6_commission_entry_order_id_stmt FROM @m6_commission_entry_order_id_ddl;
EXECUTE m6_commission_entry_order_id_stmt;
DEALLOCATE PREPARE m6_commission_entry_order_id_stmt;

-- 合伙红娘分成配置种子（固定 3 种级别；INSERT IGNORE 依赖 uk_partner_level 幂等）。
INSERT IGNORE INTO partner_level_config
    (level_id, level_name, auto_split_mode, auto_split_rate,
     promote_performance_threshold, promote_member_threshold,
     register_reward_male, register_reward_female, promoter_join_reward,
     consume_commission_mode, consume_commission_rate, share_bonus)
VALUES
    (1, '初级合伙人', 'auto_rate', 35.0000, NULL, NULL, 1.00, 1.00, 0.00, 'auto_rate', 35.0000, 1),
    (2, '中级合伙人', 'auto_rate', 40.0000, 10000.00, 100, 1.00, 1.00, 0.00, 'auto_rate', 40.0000, 1),
    (3, '战略合伙人', 'auto_rate', 45.0000, 30000.00, 500, 1.00, 1.00, 0.00, 'auto_rate', 45.0000, 1);

-- ============ M7 活动报名 / 商家联盟 / 短视频补充字段与种子 ============
-- 活动（offline_activity）按后台 UI 补字段（27 列）。
SET @m7_offline_activity_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT  1 AS ord, 'organizer' AS column_name,
                '`organizer` varchar(128) DEFAULT NULL COMMENT ''活动发起（主办方）''' AS column_ddl
        UNION ALL SELECT  2, 'time_text',
                '`time_text` varchar(128) DEFAULT NULL COMMENT ''活动时间显示文本（UI 自由文本）'''
        UNION ALL SELECT  3, 'cover_small',
                '`cover_small` varchar(255) DEFAULT NULL COMMENT ''封面小图'''
        UNION ALL SELECT  4, 'fee_name',
                '`fee_name` varchar(64) NOT NULL DEFAULT ''报名费'' COMMENT ''费用名称'''
        UNION ALL SELECT  5, 'price_male',
                '`price_male` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''男生费用'''
        UNION ALL SELECT  6, 'price_female',
                '`price_female` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''女生费用'''
        UNION ALL SELECT  7, 'signup_mode',
                '`signup_mode` varchar(24) NOT NULL DEFAULT ''anyone'' COMMENT ''anyone 任何人 / member 相亲会员'''
        UNION ALL SELECT  8, 'require_realname',
                '`require_realname` tinyint NOT NULL DEFAULT 0 COMMENT ''报名必须实名认证'''
        UNION ALL SELECT  9, 'limit_mode',
                '`limit_mode` varchar(24) NOT NULL DEFAULT ''gender'' COMMENT ''gender 限制男女人数 / total 仅限制总人数'''
        UNION ALL SELECT 10, 'max_male',
                '`max_male` int NOT NULL DEFAULT 0 COMMENT ''男生名额上限，0 不限'''
        UNION ALL SELECT 11, 'max_female',
                '`max_female` int NOT NULL DEFAULT 0 COMMENT ''女生名额上限，0 不限'''
        UNION ALL SELECT 12, 'virtual_people',
                '`virtual_people` int NOT NULL DEFAULT 0 COMMENT ''显示报名总数基数'''
        UNION ALL SELECT 13, 'virtual_female',
                '`virtual_female` int NOT NULL DEFAULT 0 COMMENT ''显示报名女生数基数'''
        UNION ALL SELECT 14, 'hide_signup_count',
                '`hide_signup_count` tinyint NOT NULL DEFAULT 0 COMMENT ''隐藏报名数'''
        UNION ALL SELECT 15, 'reward_promoter',
                '`reward_promoter` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''推广红娘奖励'''
        UNION ALL SELECT 16, 'reward_service',
                '`reward_service` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''服务红娘奖励'''
        UNION ALL SELECT 17, 'reward_partner',
                '`reward_partner` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''合伙红娘奖励'''
        UNION ALL SELECT 18, 'reminder_html',
                '`reminder_html` text COMMENT ''活动提醒（报名成功页展示）'''
        UNION ALL SELECT 19, 'service_wechat',
                '`service_wechat` varchar(64) DEFAULT NULL COMMENT ''客服微信'''
        UNION ALL SELECT 20, 'service_qr',
                '`service_qr` varchar(255) DEFAULT NULL COMMENT ''客服二维码'''
        UNION ALL SELECT 21, 'virtual_views',
                '`virtual_views` int NOT NULL DEFAULT 0 COMMENT ''浏览人气'''
        UNION ALL SELECT 22, 'sort_order',
                '`sort_order` int NOT NULL DEFAULT 0 COMMENT ''显示排序，数字越大越靠前'''
        UNION ALL SELECT 23, 'custom_share',
                '`custom_share` tinyint NOT NULL DEFAULT 0 COMMENT ''自定义分享'''
        UNION ALL SELECT 24, 'manager_ids',
                '`manager_ids` varchar(255) DEFAULT NULL COMMENT ''管理红娘 users.id 逗号分隔'''
        UNION ALL SELECT 25, 'notify_phones',
                '`notify_phones` varchar(128) DEFAULT NULL COMMENT ''报名短信通知手机号，逗号分隔'''
        UNION ALL SELECT 26, 'online',
                '`online` tinyint NOT NULL DEFAULT 1 COMMENT ''上线 1是 0否'''
        UNION ALL SELECT 27, 'audit_status',
                '`audit_status` varchar(16) NOT NULL DEFAULT ''approved'' COMMENT ''pending待审/approved通过/rejected未通过'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'offline_activity'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'offline_activity' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m7_offline_activity_ddl = IF(
    @m7_offline_activity_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `offline_activity` ', @m7_offline_activity_missing)
);
PREPARE m7_offline_activity_cols_stmt FROM @m7_offline_activity_ddl;
EXECUTE m7_offline_activity_cols_stmt;
DEALLOCATE PREPARE m7_offline_activity_cols_stmt;

-- 活动报名（activity_signup）补会员资料快照与运营字段（17 列）。
SET @m7_activity_signup_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT  1 AS ord, 'gender' AS column_name,
                '`gender` varchar(8) DEFAULT NULL COMMENT ''男/女''' AS column_ddl
        UNION ALL SELECT  2, 'age', '`age` int DEFAULT NULL'
        UNION ALL SELECT  3, 'height',
                '`height` int DEFAULT NULL COMMENT ''身高cm'''
        UNION ALL SELECT  4, 'education',
                '`education` varchar(32) DEFAULT NULL COMMENT ''学历'''
        UNION ALL SELECT  5, 'income',
                '`income` varchar(32) DEFAULT NULL COMMENT ''收入'''
        UNION ALL SELECT  6, 'marriage_status',
                '`marriage_status` varchar(32) DEFAULT NULL COMMENT ''婚况'''
        UNION ALL SELECT  7, 'company',
                '`company` varchar(128) DEFAULT NULL COMMENT ''单位'''
        UNION ALL SELECT  8, 'avatar',
                '`avatar` varchar(255) DEFAULT NULL COMMENT ''头像'''
        UNION ALL SELECT  9, 'id_card',
                '`id_card` varchar(32) DEFAULT NULL COMMENT ''身份证号'''
        UNION ALL SELECT 10, 'is_member',
                '`is_member` tinyint NOT NULL DEFAULT 0 COMMENT ''是否会员'''
        UNION ALL SELECT 11, 'is_realname',
                '`is_realname` tinyint NOT NULL DEFAULT 0 COMMENT ''是否已实名'''
        UNION ALL SELECT 12, 'signup_times',
                '`signup_times` int NOT NULL DEFAULT 1 COMMENT ''第几次报名'''
        UNION ALL SELECT 13, 'pay_status',
                '`pay_status` varchar(16) NOT NULL DEFAULT ''free'' COMMENT ''free免费/paid已支付/unpaid未支付'''
        UNION ALL SELECT 14, 'pay_amount',
                '`pay_amount` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT ''报名费金额'''
        UNION ALL SELECT 15, 'checked_in',
                '`checked_in` tinyint NOT NULL DEFAULT 0 COMMENT ''是否已签到'''
        UNION ALL SELECT 16, 'in_crm',
                '`in_crm` tinyint NOT NULL DEFAULT 0 COMMENT ''是否已入库会员CRM'''
        UNION ALL SELECT 17, 'promoter_id',
                '`promoter_id` bigint unsigned DEFAULT NULL COMMENT ''推广红娘 users.id'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @m7_activity_signup_ddl = IF(
    @m7_activity_signup_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @m7_activity_signup_missing)
);
PREPARE m7_activity_signup_cols_stmt FROM @m7_activity_signup_ddl;
EXECUTE m7_activity_signup_cols_stmt;
DEALLOCATE PREPARE m7_activity_signup_cols_stmt;

-- 幂等创建索引 idx_activity_signup_promoter / idx_offline_activity_online。
SET @m7_activity_signup_table_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
);
SET @m7_activity_signup_promoter_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'idx_activity_signup_promoter'
);
SET @m7_activity_signup_index_ddl = IF(
    @m7_activity_signup_table_exists > 0 AND @m7_activity_signup_promoter_index_exists = 0,
    'ALTER TABLE `activity_signup` ADD KEY `idx_activity_signup_promoter` (`promoter_id`)',
    'SELECT 1'
);
PREPARE m7_activity_signup_promoter_idx_stmt FROM @m7_activity_signup_index_ddl;
EXECUTE m7_activity_signup_promoter_idx_stmt;
DEALLOCATE PREPARE m7_activity_signup_promoter_idx_stmt;

SET @m7_offline_activity_table_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'offline_activity'
);
SET @m7_offline_activity_online_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'offline_activity'
      AND INDEX_NAME = 'idx_offline_activity_online'
);
SET @m7_offline_activity_index_ddl = IF(
    @m7_offline_activity_table_exists > 0 AND @m7_offline_activity_online_index_exists = 0,
    'ALTER TABLE `offline_activity` ADD KEY `idx_offline_activity_online` (`online`, `sort_order`)',
    'SELECT 1'
);
PREPARE m7_offline_activity_online_idx_stmt FROM @m7_offline_activity_index_ddl;
EXECUTE m7_offline_activity_online_idx_stmt;
DEALLOCATE PREPARE m7_offline_activity_online_idx_stmt;

-- 商家分类种子（INSERT IGNORE 依赖 uk_merchant_category_name 幂等）。
INSERT IGNORE INTO merchant_category (name, sort, status)
VALUES ('推荐餐饮', 1, 1), ('新奇体验', 2, 1), ('休闲娱乐', 3, 1), ('生活服务', 4, 1), ('结婚', 5, 1);

-- 短视频分类种子（INSERT IGNORE 依赖 uk_short_video_category_name 幂等）。
INSERT IGNORE INTO short_video_category (name, sort, status)
VALUES ('关于我们', 1, 1), ('脱单干货', 2, 1), ('活动瞬间', 3, 1), ('优质嘉宾', 4, 1), ('直播切片', 5, 1);
