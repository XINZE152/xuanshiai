-- 20261030_04_user_points_biz_key（down）
-- 删除积分流水业务幂等键；生产环境默认禁止执行。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

SET @index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND INDEX_NAME = 'uk_user_points_biz_key'
);
SET @index_ddl = IF(
    @index_exists = 0,
    'SELECT 1',
    'ALTER TABLE `user_points` DROP INDEX `uk_user_points_biz_key`'
);
PREPARE index_stmt FROM @index_ddl;
EXECUTE index_stmt;
DEALLOCATE PREPARE index_stmt;

SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND COLUMN_NAME = 'biz_key'
);
SET @column_ddl = IF(
    @column_exists = 0,
    'SELECT 1',
    'ALTER TABLE `user_points` DROP COLUMN `biz_key`'
);
PREPARE column_stmt FROM @column_ddl;
EXECUTE column_stmt;
DEALLOCATE PREPARE column_stmt;
