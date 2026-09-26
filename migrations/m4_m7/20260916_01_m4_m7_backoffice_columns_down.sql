-- M4–M7 后台运营补列迁移（down）：幂等回滚 up.sql 新增的列与索引。
-- 回滚警告：
--   1) DROP COLUMN 会永久删除这些列已写入的数据（如 customer_lead.audit_status、
--      offline_activity.online/audit_status、activity_signup 报名资料快照等）；
--   2) commission_entry.order_id 恢复 NOT NULL 仅当表中无 NULL 数据时执行，
--      存在 NULL（后台手工录入分成）时跳过该步并输出 rollback_blocked 提示；
--   3) INSERT IGNORE 写入的种子数据不在本脚本回滚范围内。
-- 幂等约定与 migrations/chat/20260923_01 相同：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE
-- 动态 DDL，可重复执行。Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ 1. 幂等删除三个索引 ============
SET @down_customer_lead_promoter_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead'
      AND INDEX_NAME = 'idx_customer_lead_promoter'
);
SET @down_customer_lead_index_ddl = IF(
    @down_customer_lead_promoter_index_exists > 0,
    'ALTER TABLE `customer_lead` DROP INDEX `idx_customer_lead_promoter`',
    'SELECT 1'
);
PREPARE down_customer_lead_promoter_idx_stmt FROM @down_customer_lead_index_ddl;
EXECUTE down_customer_lead_promoter_idx_stmt;
DEALLOCATE PREPARE down_customer_lead_promoter_idx_stmt;

SET @down_activity_signup_promoter_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'idx_activity_signup_promoter'
);
SET @down_activity_signup_index_ddl = IF(
    @down_activity_signup_promoter_index_exists > 0,
    'ALTER TABLE `activity_signup` DROP INDEX `idx_activity_signup_promoter`',
    'SELECT 1'
);
PREPARE down_activity_signup_promoter_idx_stmt FROM @down_activity_signup_index_ddl;
EXECUTE down_activity_signup_promoter_idx_stmt;
DEALLOCATE PREPARE down_activity_signup_promoter_idx_stmt;

SET @down_offline_activity_online_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'offline_activity'
      AND INDEX_NAME = 'idx_offline_activity_online'
);
SET @down_offline_activity_index_ddl = IF(
    @down_offline_activity_online_index_exists > 0,
    'ALTER TABLE `offline_activity` DROP INDEX `idx_offline_activity_online`',
    'SELECT 1'
);
PREPARE down_offline_activity_online_idx_stmt FROM @down_offline_activity_index_ddl;
EXECUTE down_offline_activity_online_idx_stmt;
DEALLOCATE PREPARE down_offline_activity_online_idx_stmt;

-- ============ 2. commission_entry.order_id 恢复 NOT NULL（仅当无 NULL 数据） ============
SET @down_order_id_nullable = (
    SELECT IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'commission_entry' AND COLUMN_NAME = 'order_id'
    LIMIT 1
);
-- 列存在且可空时才统计 NULL 行数；列不存在或已是非空时安全置 0。
SET @down_order_id_count_sql = IF(
    @down_order_id_nullable = 'YES',
    'SELECT COUNT(*) INTO @down_order_id_null_rows FROM `commission_entry` WHERE `order_id` IS NULL',
    'SELECT 0 INTO @down_order_id_null_rows'
);
PREPARE down_order_id_count_stmt FROM @down_order_id_count_sql;
EXECUTE down_order_id_count_stmt;
DEALLOCATE PREPARE down_order_id_count_stmt;

SET @down_order_id_restore_ddl = IF(
    @down_order_id_nullable = 'YES' AND @down_order_id_null_rows = 0,
    'ALTER TABLE `commission_entry` MODIFY COLUMN `order_id` bigint unsigned NOT NULL',
    IF(
        @down_order_id_nullable = 'YES' AND @down_order_id_null_rows > 0,
        'SELECT ''commission_entry.order_id 存在 NULL 数据，跳过恢复 NOT NULL：请先处理手工录入分成数据'' AS rollback_blocked',
        'SELECT 1'
    )
);
PREPARE down_order_id_restore_stmt FROM @down_order_id_restore_ddl;
EXECUTE down_order_id_restore_stmt;
DEALLOCATE PREPARE down_order_id_restore_stmt;

-- ============ 3. 幂等删除 up 新增的补列（仅删除当前存在的列） ============
SET @down_customer_lead_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'promoter_id' AS column_name
        UNION ALL
        SELECT 2, 'audit_status'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
);
SET @down_customer_lead_ddl = IF(
    @down_customer_lead_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @down_customer_lead_drops)
);
PREPARE down_customer_lead_cols_stmt FROM @down_customer_lead_ddl;
EXECUTE down_customer_lead_cols_stmt;
DEALLOCATE PREPARE down_customer_lead_cols_stmt;

SET @down_meeting_record_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'member_visible' AS column_name
        UNION ALL
        SELECT 2, 'sms_remind'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'meeting_record'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'meeting_record' AND c.COLUMN_NAME = w.column_name
);
SET @down_meeting_record_ddl = IF(
    @down_meeting_record_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `meeting_record` ', @down_meeting_record_drops)
);
PREPARE down_meeting_record_cols_stmt FROM @down_meeting_record_ddl;
EXECUTE down_meeting_record_cols_stmt;
DEALLOCATE PREPARE down_meeting_record_cols_stmt;

SET @down_organization_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'link_url' AS column_name
        UNION ALL
        SELECT 2, 'sort_order'
        UNION ALL
        SELECT 3, 'qr_code'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'organization'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'organization' AND c.COLUMN_NAME = w.column_name
);
SET @down_organization_ddl = IF(
    @down_organization_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `organization` ', @down_organization_drops)
);
PREPARE down_organization_cols_stmt FROM @down_organization_ddl;
EXECUTE down_organization_cols_stmt;
DEALLOCATE PREPARE down_organization_cols_stmt;

SET @down_partner_team_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'level_id' AS column_name
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'partner_team'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'partner_team' AND c.COLUMN_NAME = w.column_name
);
SET @down_partner_team_ddl = IF(
    @down_partner_team_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `partner_team` ', @down_partner_team_drops)
);
PREPARE down_partner_team_cols_stmt FROM @down_partner_team_ddl;
EXECUTE down_partner_team_cols_stmt;
DEALLOCATE PREPARE down_partner_team_cols_stmt;

SET @down_commission_entry_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'source' AS column_name
        UNION ALL
        SELECT 2, 'remark'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'commission_entry'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'commission_entry' AND c.COLUMN_NAME = w.column_name
);
SET @down_commission_entry_ddl = IF(
    @down_commission_entry_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `commission_entry` ', @down_commission_entry_drops)
);
PREPARE down_commission_entry_cols_stmt FROM @down_commission_entry_ddl;
EXECUTE down_commission_entry_cols_stmt;
DEALLOCATE PREPARE down_commission_entry_cols_stmt;

SET @down_offline_activity_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT  1 AS ord, 'organizer' AS column_name
        UNION ALL SELECT  2, 'time_text'
        UNION ALL SELECT  3, 'cover_small'
        UNION ALL SELECT  4, 'fee_name'
        UNION ALL SELECT  5, 'price_male'
        UNION ALL SELECT  6, 'price_female'
        UNION ALL SELECT  7, 'signup_mode'
        UNION ALL SELECT  8, 'require_realname'
        UNION ALL SELECT  9, 'limit_mode'
        UNION ALL SELECT 10, 'max_male'
        UNION ALL SELECT 11, 'max_female'
        UNION ALL SELECT 12, 'virtual_people'
        UNION ALL SELECT 13, 'virtual_female'
        UNION ALL SELECT 14, 'hide_signup_count'
        UNION ALL SELECT 15, 'reward_promoter'
        UNION ALL SELECT 16, 'reward_service'
        UNION ALL SELECT 17, 'reward_partner'
        UNION ALL SELECT 18, 'reminder_html'
        UNION ALL SELECT 19, 'service_wechat'
        UNION ALL SELECT 20, 'service_qr'
        UNION ALL SELECT 21, 'virtual_views'
        UNION ALL SELECT 22, 'sort_order'
        UNION ALL SELECT 23, 'custom_share'
        UNION ALL SELECT 24, 'manager_ids'
        UNION ALL SELECT 25, 'notify_phones'
        UNION ALL SELECT 26, 'online'
        UNION ALL SELECT 27, 'audit_status'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'offline_activity'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'offline_activity' AND c.COLUMN_NAME = w.column_name
);
SET @down_offline_activity_ddl = IF(
    @down_offline_activity_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `offline_activity` ', @down_offline_activity_drops)
);
PREPARE down_offline_activity_cols_stmt FROM @down_offline_activity_ddl;
EXECUTE down_offline_activity_cols_stmt;
DEALLOCATE PREPARE down_offline_activity_cols_stmt;

SET @down_activity_signup_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT  1 AS ord, 'gender' AS column_name
        UNION ALL SELECT  2, 'age'
        UNION ALL SELECT  3, 'height'
        UNION ALL SELECT  4, 'education'
        UNION ALL SELECT  5, 'income'
        UNION ALL SELECT  6, 'marriage_status'
        UNION ALL SELECT  7, 'company'
        UNION ALL SELECT  8, 'avatar'
        UNION ALL SELECT  9, 'id_card'
        UNION ALL SELECT 10, 'is_member'
        UNION ALL SELECT 11, 'is_realname'
        UNION ALL SELECT 12, 'signup_times'
        UNION ALL SELECT 13, 'pay_status'
        UNION ALL SELECT 14, 'pay_amount'
        UNION ALL SELECT 15, 'checked_in'
        UNION ALL SELECT 16, 'in_crm'
        UNION ALL SELECT 17, 'promoter_id'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
);
SET @down_activity_signup_ddl = IF(
    @down_activity_signup_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @down_activity_signup_drops)
);
PREPARE down_activity_signup_cols_stmt FROM @down_activity_signup_ddl;
EXECUTE down_activity_signup_cols_stmt;
DEALLOCATE PREPARE down_activity_signup_cols_stmt;
