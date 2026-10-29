-- 20261030_01_payment_v3_core 回滚后校验。
-- 返回 0 行表示 down 已恢复旧结构；任何返回行都表示回滚不完整。

SELECT expected.table_name,
       expected.column_name,
       'column not removed' AS issue
FROM (
    SELECT 'payment_order' AS table_name, 'channel' AS column_name
    UNION ALL SELECT 'payment_order', 'currency'
    UNION ALL SELECT 'payment_order', 'paid_amount'
    UNION ALL SELECT 'payment_order', 'prepay_id'
    UNION ALL SELECT 'payment_order', 'payer_openid'
    UNION ALL SELECT 'payment_order', 'trade_state'
    UNION ALL SELECT 'payment_order', 'mchid'
    UNION ALL SELECT 'payment_order', 'appid'
    UNION ALL SELECT 'payment_order', 'fulfillment_status'
    UNION ALL SELECT 'payment_order', 'fulfillment_error'
    UNION ALL SELECT 'payment_order', 'refund_status'
    UNION ALL SELECT 'payment_order', 'refunded_amount'
    UNION ALL SELECT 'payment_order', 'closed_at'
) AS expected
JOIN INFORMATION_SCHEMA.COLUMNS actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
 AND actual.COLUMN_NAME = expected.column_name

UNION ALL

SELECT 'payment_order', 'uk_payment_order_channel_tx', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'uk_payment_order_channel_tx'
)

UNION ALL

SELECT 'payment_order', 'idx_payment_order_fulfillment', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_fulfillment'
)

UNION ALL

SELECT 'payment_order', 'idx_payment_order_expire', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order'
      AND INDEX_NAME = 'idx_payment_order_expire'
)

UNION ALL

SELECT expected.table_name, NULL, 'table not removed'
FROM (
    SELECT 'payment_refund' AS table_name
    UNION ALL SELECT 'payment_fulfillment_job'
    UNION ALL SELECT 'payment_notify_log'
) AS expected
JOIN INFORMATION_SCHEMA.TABLES actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name;
