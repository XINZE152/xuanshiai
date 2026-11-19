-- =============================================================================
-- 客源线索资料列迁移（down）
--
-- 回滚 20261029_01_lead_profile_columns_up.sql：删除 customer_lead 的
-- real_name / photo / birthday 三列。
--
-- 警告：DROP COLUMN 会永久丢弃已填写的真实姓名、照片 URL 和出生日期；回滚后
-- app/services/customer_lead_admin.py 与 app/services/matchmaker_workspace.py
-- 的客源查询会立刻报 MySQL 1054（这两处按列名无条件引用），必须先回退对应代码。
-- 可重复执行：列不存在时跳过。
-- =============================================================================

SET NAMES utf8mb4;

SET @cl_profile_drop = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', c.COLUMN_NAME, '`') ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'real_name' AS column_name
        UNION ALL SELECT 2 AS ord, 'photo'
        UNION ALL SELECT 3 AS ord, 'birthday'
    ) AS w
    JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
);
SET @cl_profile_drop_ddl = IF(@cl_profile_drop IS NULL, 'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @cl_profile_drop));
PREPARE cl_profile_drop_stmt FROM @cl_profile_drop_ddl;
EXECUTE cl_profile_drop_stmt;
DEALLOCATE PREPARE cl_profile_drop_stmt;
