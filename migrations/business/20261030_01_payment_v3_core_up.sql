-- 20261030_01_payment_v3_core
-- 微信支付 APIv3 改造：payment_order 扩展 + 退款单 + 履约任务 + 回调日志。
-- 幂等约定：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE；已存在则跳过。
-- Run with the intended database selected (MySQL 8+).
--
-- 注意：
-- 1) 新增 uk_payment_order_channel_tx 前，请先确认历史 order_no/transaction_id 无重复；
--    若存在重复，本迁移会在 ADD UNIQUE 处失败，需先清理再重跑。
-- 2) down 会删除新增列及其数据，生产环境默认禁止执行。

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ 1. payment_order：新增列 ============
SET @payment_order_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'channel' AS column_name,
               '`channel` varchar(16) NOT NULL DEFAULT ''wechat'' COMMENT ''支付渠道 wechat/alipay/balance/mock/legacy''' AS column_ddl
        UNION ALL SELECT 2, 'currency', '`currency` char(3) NOT NULL DEFAULT ''CNY'''
        UNION ALL SELECT 3, 'paid_amount', '`paid_amount` decimal(12,2) DEFAULT NULL COMMENT ''渠道实收金额'''
        UNION ALL SELECT 4, 'prepay_id', '`prepay_id` varchar(64) DEFAULT NULL COMMENT ''微信预支付ID'''
        UNION ALL SELECT 5, 'payer_openid', '`payer_openid` varchar(64) DEFAULT NULL COMMENT ''付款人 openid'''
        UNION ALL SELECT 6, 'trade_state', '`trade_state` varchar(32) DEFAULT NULL COMMENT ''SUCCESS/REFUND/NOTPAY/CLOSED/REVOKED/USERPAYING/PAYERROR'''
        UNION ALL SELECT 7, 'mchid', '`mchid` varchar(32) DEFAULT NULL COMMENT ''微信商户号'''
        UNION ALL SELECT 8, 'appid', '`appid` varchar(32) DEFAULT NULL COMMENT ''小程序 AppID'''
        UNION ALL SELECT 9, 'fulfillment_status', '`fulfillment_status` varchar(16) NOT NULL DEFAULT ''PENDING'' COMMENT ''PENDING/PROCESSING/SUCCESS/FAILED/DEAD'''
        UNION ALL SELECT 10, 'fulfillment_error', '`fulfillment_error` varchar(500) DEFAULT NULL'
        UNION ALL SELECT 11, 'refund_status', '`refund_status` varchar(16) NOT NULL DEFAULT ''NONE'' COMMENT ''NONE/PARTIAL/FULL'''
        UNION ALL SELECT 12, 'refunded_amount', '`refunded_amount` decimal(12,2) NOT NULL DEFAULT 0.00'
        UNION ALL SELECT 13, 'closed_at', '`closed_at` datetime DEFAULT NULL'
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'payment_order'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'payment_order' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @payment_order_ddl = IF(
    @payment_order_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `payment_order` ', @payment_order_missing)
);
PREPARE payment_order_stmt FROM @payment_order_ddl;
EXECUTE payment_order_stmt;
DEALLOCATE PREPARE payment_order_stmt;

-- ============ 1.1 历史订单回填 ============
-- 旧数据没有 channel/fulfillment/refund 口径，按既有 pay_type/状态回填：
-- - pay_type=4 是测试支付；pay_type=2 是余额支付；
-- - TEST-/sandbox- 交易号归为 mock/legacy，避免进入真实微信对账；
-- - 已支付/已退款历史订单视为已履约；历史 status=3 只回收权益，
--   不是微信退款，因此只记入 refund_status=FULL，不伪造 payment_refund。
UPDATE `payment_order`
SET `channel` = 'mock'
WHERE `channel` = 'wechat' AND `pay_type` = 4;

UPDATE `payment_order`
SET `channel` = 'balance'
WHERE `channel` = 'wechat' AND `pay_type` = 2;

UPDATE `payment_order`
SET `channel` = 'legacy'
WHERE `channel` = 'wechat'
  AND (`transaction_id` LIKE 'sandbox-%' OR `transaction_id` LIKE 'TEST-%');

UPDATE `payment_order`
SET `fulfillment_status` = 'SUCCESS'
WHERE `status` IN (1, 3) AND `fulfillment_status` = 'PENDING';

UPDATE `payment_order`
SET `refund_status` = 'FULL', `refunded_amount` = `amount`
WHERE `status` = 3 AND `refund_status` = 'NONE';

-- ============ 2. payment_order：新增索引 ============
SET @idx_channel_tx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'uk_payment_order_channel_tx'
);
SET @idx_channel_tx_ddl = IF(
    @idx_channel_tx_exists = 0,
    'ALTER TABLE `payment_order` ADD UNIQUE KEY `uk_payment_order_channel_tx` (`channel`,`transaction_id`)',
    'SELECT 1'
);
PREPARE idx_channel_tx_stmt FROM @idx_channel_tx_ddl;
EXECUTE idx_channel_tx_stmt;
DEALLOCATE PREPARE idx_channel_tx_stmt;

SET @idx_fulfillment_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_fulfillment'
);
SET @idx_fulfillment_ddl = IF(
    @idx_fulfillment_exists = 0,
    'ALTER TABLE `payment_order` ADD KEY `idx_payment_order_fulfillment` (`fulfillment_status`,`updated_at`)',
    'SELECT 1'
);
PREPARE idx_fulfillment_stmt FROM @idx_fulfillment_ddl;
EXECUTE idx_fulfillment_stmt;
DEALLOCATE PREPARE idx_fulfillment_stmt;

SET @idx_expire_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_expire'
);
SET @idx_expire_ddl = IF(
    @idx_expire_exists = 0,
    'ALTER TABLE `payment_order` ADD KEY `idx_payment_order_expire` (`status`,`expire_at`)',
    'SELECT 1'
);
PREPARE idx_expire_stmt FROM @idx_expire_ddl;
EXECUTE idx_expire_stmt;
DEALLOCATE PREPARE idx_expire_stmt;

-- ============ 3. 退款单 ============
CREATE TABLE IF NOT EXISTS `payment_refund` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `refund_no` varchar(64) NOT NULL COMMENT '商户退款单号 out_refund_no',
    `order_id` bigint unsigned NOT NULL COMMENT 'payment_order.id',
    `order_no` varchar(64) NOT NULL COMMENT '原支付订单号快照',
    `user_id` bigint unsigned NOT NULL COMMENT '付款用户',
    `channel` varchar(16) NOT NULL DEFAULT 'wechat' COMMENT 'wechat/alipay/legacy',
    `transaction_id` varchar(64) DEFAULT NULL COMMENT '微信支付交易号',
    `wechat_refund_id` varchar(64) DEFAULT NULL COMMENT '微信退款单号 refund_id',
    `order_amount` decimal(12,2) NOT NULL COMMENT '原订单金额快照',
    `refund_amount` decimal(12,2) NOT NULL COMMENT '本次退款金额',
    `currency` char(3) NOT NULL DEFAULT 'CNY',
    `refund_type` varchar(16) NOT NULL DEFAULT 'FULL' COMMENT 'FULL/PARTIAL',
    `status` varchar(24) NOT NULL DEFAULT 'CREATED' COMMENT 'CREATED/PROCESSING/SUCCESS/FAILED/CLOSED',
    `reason` varchar(255) NOT NULL COMMENT '退款原因',
    `operator_id` bigint unsigned DEFAULT NULL COMMENT '后台操作人 matchmaker_admin_account.id',
    `idempotency_key` varchar(128) NOT NULL COMMENT '后台幂等键',
    `notify_id` varchar(64) DEFAULT NULL COMMENT '微信退款通知 id，去重',
    `success_time` datetime DEFAULT NULL,
    `fail_reason` varchar(255) DEFAULT NULL,
    `attempt_count` int unsigned NOT NULL DEFAULT 0,
    `next_retry_at` datetime DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_payment_refund_no` (`refund_no`),
    UNIQUE KEY `uk_payment_refund_idem` (`order_id`, `idempotency_key`),
    UNIQUE KEY `uk_payment_refund_notify` (`notify_id`),
    KEY `idx_payment_refund_order` (`order_id`),
    KEY `idx_payment_refund_status` (`status`, `next_retry_at`),
    KEY `idx_payment_refund_tx` (`channel`, `transaction_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='支付退款单';

-- ============ 4. 履约任务（事务性 Outbox） ============
CREATE TABLE IF NOT EXISTS `payment_fulfillment_job` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `biz_key` varchar(128) NOT NULL COMMENT '幂等业务键：FULFILL:{order_id} / REVOKE:{refund_no}',
    `order_id` bigint unsigned NOT NULL,
    `order_no` varchar(64) NOT NULL,
    `order_type` tinyint NOT NULL COMMENT '1会员 2爆灯 3服务单 4置顶 5活动 6虚拟商品',
    `job_type` varchar(16) NOT NULL DEFAULT 'FULFILL' COMMENT 'FULFILL/REVOKE',
    `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/SUCCESS/FAILED/DEAD',
    `attempt_count` int unsigned NOT NULL DEFAULT 0,
    `next_retry_at` datetime DEFAULT NULL,
    `lease_owner` varchar(64) DEFAULT NULL,
    `lease_expire_at` datetime DEFAULT NULL,
    `last_error` varchar(500) DEFAULT NULL,
    `payload_json` json DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_payment_fulfillment_biz_key` (`biz_key`),
    KEY `idx_payment_fulfillment_claim` (`status`, `next_retry_at`, `id`),
    KEY `idx_payment_fulfillment_order` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='支付履约/退款回收任务（事务性 Outbox）';

-- ============ 5. 回调日志/防重放 ============
CREATE TABLE IF NOT EXISTS `payment_notify_log` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `notify_id` varchar(64) NOT NULL COMMENT '微信通知 id',
    `event_type` varchar(64) NOT NULL COMMENT 'TRANSACTION.SUCCESS / REFUND.SUCCESS ...',
    `channel` varchar(16) NOT NULL DEFAULT 'wechat',
    `out_trade_no` varchar(64) DEFAULT NULL,
    `transaction_id` varchar(64) DEFAULT NULL,
    `refund_no` varchar(64) DEFAULT NULL,
    `signature_serial` varchar(64) DEFAULT NULL COMMENT 'Wechatpay-Serial',
    `body_sha256` char(64) NOT NULL COMMENT '原始请求体 SHA256',
    `payload_json` json DEFAULT NULL COMMENT '解密后的 resource 明文（仅内部排障，按保留策略清理）',
    `verified` tinyint(1) NOT NULL DEFAULT 0,
    `processed` tinyint(1) NOT NULL DEFAULT 0,
    `error` varchar(500) DEFAULT NULL,
    `received_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `processed_at` datetime DEFAULT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_payment_notify_id` (`notify_id`),
    KEY `idx_payment_notify_order` (`out_trade_no`),
    KEY `idx_payment_notify_status` (`processed`, `received_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='微信支付/退款回调日志';
