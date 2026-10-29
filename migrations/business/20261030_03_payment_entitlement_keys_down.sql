-- 20261030_03_payment_entitlement_keys（down）
-- 删除活动支付字段与履约唯一键；生产环境默认禁止执行。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_boost'
      AND INDEX_NAME = 'uk_user_boost_order_no'
);
SET @idx_ddl = IF(
    @idx_exists = 0, 'SELECT 1',
    'ALTER TABLE `user_boost` DROP INDEX `uk_user_boost_order_no`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership'
      AND INDEX_NAME = 'uk_user_membership_order_no'
);
SET @idx_ddl = IF(
    @idx_exists = 0, 'SELECT 1',
    'ALTER TABLE `user_membership` DROP INDEX `uk_user_membership_order_no`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'uk_activity_signup_order'
);
SET @idx_ddl = IF(
    @idx_exists = 0, 'SELECT 1',
    'ALTER TABLE `activity_signup` DROP INDEX `uk_activity_signup_order`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @activity_signup_columns = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'activity_signup'
      AND COLUMN_NAME IN ('order_no', 'pay_status', 'paid_amount')
);
SET @activity_signup_ddl = IF(
    @activity_signup_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @activity_signup_columns)
);
PREPARE activity_signup_stmt FROM @activity_signup_ddl;
EXECUTE activity_signup_stmt;
DEALLOCATE PREPARE activity_signup_stmt;
