-- Q66 (2026-11-10): 积分兑换支持数量（1~10）。
-- 幂等约定：INFORMATION_SCHEMA 检查 + 动态 DDL；列已存在时跳过。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

SET @count_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'point_redeem_order'
      AND COLUMN_NAME = 'count'
);
SET @count_ddl = IF(
    @count_column_exists = 0,
    'ALTER TABLE `point_redeem_order` ADD COLUMN `count` int unsigned NOT NULL DEFAULT 1 COMMENT ''兑换数量'' AFTER `points_cost`',
    'SELECT 1'
);
PREPARE count_stmt FROM @count_ddl;
EXECUTE count_stmt;
DEALLOCATE PREPARE count_stmt;
