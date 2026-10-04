-- =============================================================================
-- 活动报名软删除列迁移（up）
--
-- 背景：`activity_signup` 的删除原为物理删除且无审计，报名记录含支付金额与
--       签到状态，删除后无法追溯、无法支撑退款对账。本迁移补 `deleted_at` /
--       `deleted_by` 两列与 `idx_activity_signup_deleted` 索引，配合应用层改为
--       软删除 + `business_audit_log` 快照。
--
-- 幂等约定与 migrations/m4_m7/20260916_01 相同：INFORMATION_SCHEMA 检查 +
-- PREPARE/EXECUTE 动态 DDL，可重复执行；表不存在时静默跳过。
-- 请在已选定目标数据库的会话中执行（MySQL 8+）。
-- =============================================================================

SET NAMES utf8mb4;

-- 1. 幂等补列：deleted_at / deleted_by
SET @soft_delete_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'deleted_at' AS column_name,
               '`deleted_at` datetime DEFAULT NULL COMMENT ''软删除时间，非空表示已删除''' AS column_ddl
        UNION ALL SELECT 2, 'deleted_by',
               '`deleted_by` bigint unsigned DEFAULT NULL COMMENT ''删除操作人 matchmaker_admin_account.id'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @soft_delete_ddl = IF(
    @soft_delete_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @soft_delete_missing)
);
PREPARE soft_delete_cols_stmt FROM @soft_delete_ddl;
EXECUTE soft_delete_cols_stmt;
DEALLOCATE PREPARE soft_delete_cols_stmt;

-- 2. 幂等创建索引 idx_activity_signup_deleted
SET @soft_delete_table_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
);
SET @soft_delete_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'idx_activity_signup_deleted'
);
SET @soft_delete_index_ddl = IF(
    @soft_delete_table_exists > 0 AND @soft_delete_index_exists = 0,
    'ALTER TABLE `activity_signup` ADD KEY `idx_activity_signup_deleted` (`deleted_at`)',
    'SELECT 1'
);
PREPARE soft_delete_idx_stmt FROM @soft_delete_index_ddl;
EXECUTE soft_delete_idx_stmt;
DEALLOCATE PREPARE soft_delete_idx_stmt;
