-- =============================================================================
-- 活动报名软删除列迁移（down）
--
-- 回滚 `20261004_01_activity_signup_soft_delete_up.sql`：幂等删除索引
-- `idx_activity_signup_deleted` 与 `deleted_at` / `deleted_by` 两列。
--
-- 注意：**会永久删除已软删除报名的删除标记**（`deleted_at` / `deleted_by`），
--       这些行将重新出现在后台列表中。只在确认放弃该审计信息后使用。
--       可重复执行。
-- =============================================================================

SET NAMES utf8mb4;

-- 1. 幂等删除索引
SET @down_soft_delete_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'idx_activity_signup_deleted'
);
SET @down_soft_delete_index_ddl = IF(
    @down_soft_delete_index_exists > 0,
    'ALTER TABLE `activity_signup` DROP INDEX `idx_activity_signup_deleted`',
    'SELECT 1'
);
PREPARE down_soft_delete_idx_stmt FROM @down_soft_delete_index_ddl;
EXECUTE down_soft_delete_idx_stmt;
DEALLOCATE PREPARE down_soft_delete_idx_stmt;

-- 2. 幂等删除两列
SET @down_soft_delete_drops = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', w.column_name, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'deleted_at' AS column_name
        UNION ALL SELECT 2, 'deleted_by'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
);
SET @down_soft_delete_ddl = IF(
    @down_soft_delete_drops IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @down_soft_delete_drops)
);
PREPARE down_soft_delete_cols_stmt FROM @down_soft_delete_ddl;
EXECUTE down_soft_delete_cols_stmt;
DEALLOCATE PREPARE down_soft_delete_cols_stmt;
