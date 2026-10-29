-- 20261030_04_user_points_biz_key
-- 给积分流水增加业务幂等键，彻底消除虚拟商品重复入账。
-- 历史行 biz_key 为 NULL；MySQL 唯一索引允许多个 NULL，不影响存量数据。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND COLUMN_NAME = 'biz_key'
);
SET @column_ddl = IF(
    @column_exists = 0,
    'ALTER TABLE `user_points` ADD COLUMN `biz_key` varchar(128) DEFAULT NULL COMMENT ''业务幂等键，如 POINTS:{订单号}/REFUND:{订单号}''',
    'SELECT 1'
);
PREPARE column_stmt FROM @column_ddl;
EXECUTE column_stmt;
DEALLOCATE PREPARE column_stmt;

SET @index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND INDEX_NAME = 'uk_user_points_biz_key'
);
SET @index_ddl = IF(
    @index_exists = 0,
    'ALTER TABLE `user_points` ADD UNIQUE KEY `uk_user_points_biz_key` (`biz_key`)',
    'SELECT 1'
);
PREPARE index_stmt FROM @index_ddl;
EXECUTE index_stmt;
DEALLOCATE PREPARE index_stmt;
