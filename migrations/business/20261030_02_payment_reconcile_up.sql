-- 20261030_02_payment_reconcile
-- 微信支付对账批次与差异表。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `payment_reconcile_batch` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `bill_date` date NOT NULL COMMENT '账单日期',
    `bill_type` varchar(16) NOT NULL COMMENT 'TRADE交易 / REFUND退款',
    `channel` varchar(16) NOT NULL DEFAULT 'wechat',
    `file_name` varchar(255) DEFAULT NULL,
    `file_sha256` char(64) DEFAULT NULL,
    `record_count` int unsigned NOT NULL DEFAULT 0,
    `total_amount` decimal(14,2) NOT NULL DEFAULT 0.00,
    `status` varchar(16) NOT NULL DEFAULT 'DOWNLOADED' COMMENT 'DOWNLOADED/PARSED/RECONCILED/FAILED',
    `diff_count` int unsigned NOT NULL DEFAULT 0,
    `error` varchar(500) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_payment_reconcile_batch` (`bill_date`, `bill_type`, `channel`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='支付对账批次';

CREATE TABLE IF NOT EXISTS `payment_reconcile_diff` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `batch_id` bigint unsigned NOT NULL,
    `bill_date` date NOT NULL,
    `diff_type` varchar(32) NOT NULL COMMENT 'LOCAL_ONLY/REMOTE_ONLY/AMOUNT_MISMATCH/STATUS_MISMATCH/REFUND_MISMATCH',
    `order_no` varchar(64) DEFAULT NULL,
    `refund_no` varchar(64) DEFAULT NULL,
    `transaction_id` varchar(64) DEFAULT NULL,
    `local_amount` decimal(12,2) DEFAULT NULL,
    `remote_amount` decimal(12,2) DEFAULT NULL,
    `local_status` varchar(24) DEFAULT NULL,
    `remote_status` varchar(24) DEFAULT NULL,
    `resolution` varchar(16) NOT NULL DEFAULT 'OPEN' COMMENT 'OPEN/IGNORED/RESOLVED',
    `resolved_by` bigint unsigned DEFAULT NULL,
    `resolved_at` datetime DEFAULT NULL,
    `remark` varchar(255) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_payment_diff_batch` (`batch_id`),
    KEY `idx_payment_diff_status` (`resolution`, `bill_date`),
    KEY `idx_payment_diff_order` (`order_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='支付对账差异';
