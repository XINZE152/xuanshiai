-- =============================================================================
-- 客源线索「本人资料」补列迁移（2026-10-28）（down）
--
-- 回滚 20261028_01_lead_profile_up.sql：删除本次新增的 12 个客源本人资料列。
-- 只删本迁移补入的列，不动 filters/20261011_01 的 age/height/marriage/education/
-- occupation/residence/hometown，也不动 gender。
--
-- 警告：DROP COLUMN 会永久丢弃已登记的客源资料数据，执行前必须先备份。
-- 可重复执行：列不存在时跳过。
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @customer_lead_profile_drop = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', c.COLUMN_NAME, '`') ORDER BY c.ORDINATE_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS c
    WHERE c.TABLE_SCHEMA = DATABASE()
      AND c.TABLE_NAME = 'customer_lead'
      AND c.COLUMN_NAME IN (
          'real_name', 'birth_date', 'weight', 'income', 'house', 'car',
          'smoking', 'drinking', 'ethnicity', 'school', 'company', 'photos'
      )
);
SET @customer_lead_profile_down_ddl = IF(
    @customer_lead_profile_drop IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @customer_lead_profile_drop)
);
PREPARE customer_lead_profile_down_stmt FROM @customer_lead_profile_down_ddl;
EXECUTE customer_lead_profile_down_stmt;
DEALLOCATE PREPARE customer_lead_profile_down_stmt;
