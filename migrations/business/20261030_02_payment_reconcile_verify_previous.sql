-- 20261030_02_payment_reconcile 回滚后校验。
-- 返回 0 行表示 down 已恢复旧结构。

SELECT expected.table_name, NULL AS column_name, 'table not removed' AS issue
FROM (
    SELECT 'payment_reconcile_batch' AS table_name
    UNION ALL SELECT 'payment_reconcile_diff'
) AS expected
JOIN INFORMATION_SCHEMA.TABLES actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name;
