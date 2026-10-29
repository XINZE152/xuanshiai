-- 20261030_01_payment_v3_core（down）
--
-- 本迁移会删除 payment_order 新增列及其数据，并删除退款/履约/回调日志表。
-- 生产环境默认禁止执行；仅在隔离库回滚或已确认无数据时使用。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

-- ============ 1. 删除新表 ============
DROP TABLE IF EXISTS `payment_notify_log`;
DROP TABLE IF EXISTS `payment_fulfillment_job`;
DROP TABLE IF EXISTS `payment_refund`;

-- ============ 2. 删除 payment_order 索引 ============
SET @idx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'uk_payment_order_channel_tx'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'SELECT 1',
    'ALTER TABLE `payment_order` DROP INDEX `uk_payment_order_channel_tx`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @idx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_fulfillment'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'SELECT 1',
    'ALTER TABLE `payment_order` DROP INDEX `idx_payment_order_fulfillment`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

SET @idx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_expire'
);
SET @idx_ddl = IF(
    @idx_exists = 0,
    'SELECT 1',
    'ALTER TABLE `payment_order` DROP INDEX `idx_payment_order_expire`'
);
PREPARE idx_stmt FROM @idx_ddl;
EXECUTE idx_stmt;
DEALLOCATE PREPARE idx_stmt;

-- ============ 3. 删除 payment_order 新增列 ============
SET @payment_order_columns = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'payment_order'
      AND COLUMN_NAME IN (
          'channel', 'currency', 'paid_amount', 'prepay_id', 'payer_openid',
          'trade_state', 'mchid', 'appid', 'fulfillment_status',
          'fulfillment_error', 'refund_status', 'refunded_amount', 'closed_at'
      )
);
SET @payment_order_ddl = IF(
    @payment_order_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `payment_order` ', @payment_order_columns)
);
PREPARE payment_order_stmt FROM @payment_order_ddl;
EXECUTE payment_order_stmt;
DEALLOCATE PREPARE payment_order_stmt;
