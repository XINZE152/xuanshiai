-- 20261030_03_payment_entitlement_keys
-- 履约 Handler 依赖的唯一键：会员/爆灯按订单号幂等；活动报名补支付字段。
--
-- 执行前请先查重（有重复会导致 ADD UNIQUE 失败）：
--   SELECT order_no, COUNT(*) FROM user_membership
--    WHERE order_no IS NOT NULL GROUP BY order_no HAVING COUNT(*) > 1;
--   SELECT order_no, COUNT(*) FROM user_boost
--    WHERE order_no IS NOT NULL GROUP BY order_no HAVING COUNT(*) > 1;
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ 1. activity_signup：支付字段 ============
SET @activity_signup_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'order_no' AS column_name,
               '`order_no` varchar(64) DEFAULT NULL COMMENT ''支付订单号''' AS column_ddl
        UNION ALL SELECT 2, 'pay_status', '`pay_status` varchar(16) NOT NULL DEFAULT ''unpaid'' COMMENT ''unpaid/paid/refunded'''
        UNION ALL SELECT 3, 'paid_amount', '`paid_amount` decimal(12,2) NOT NULL DEFAULT 0.00'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @activity_signup_ddl = IF(
    @activity_signup_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `activity_signup` ', @activity_signup_missing)
);
PREPARE activity_signup_stmt FROM @activity_signup_ddl;
EXECUTE activity_signup_stmt;
DEALLOCATE PREPARE activity_signup_stmt;

SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'uk_activity_signup_order'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'ALTER TABLE `activity_signup` ADD UNIQUE KEY `uk_activity_signup_order` (`order_no`)',
    'SELECT 1'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

-- ============ 2. user_membership：订单号唯一 ============
SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership'
      AND INDEX_NAME = 'uk_user_membership_order_no'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'ALTER TABLE `user_membership` ADD UNIQUE KEY `uk_user_membership_order_no` (`order_no`)',
    'SELECT 1'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

-- ============ 3. user_boost：订单号唯一 ============
SET @idx_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_boost'
      AND INDEX_NAME = 'uk_user_boost_order_no'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'ALTER TABLE `user_boost` ADD UNIQUE KEY `uk_user_boost_order_no` (`order_no`)',
    'SELECT 1'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;
