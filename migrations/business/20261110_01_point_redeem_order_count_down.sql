-- Q66 rollback: remove point_redeem_order.count when present.

SET NAMES utf8mb4;

SET @count_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'point_redeem_order'
      AND COLUMN_NAME = 'count'
);
SET @count_ddl = IF(
    @count_column_exists = 1,
    'ALTER TABLE `point_redeem_order` DROP COLUMN `count`',
    'SELECT 1'
);
PREPARE count_stmt FROM @count_ddl;
EXECUTE count_stmt;
DEALLOCATE PREPARE count_stmt;
