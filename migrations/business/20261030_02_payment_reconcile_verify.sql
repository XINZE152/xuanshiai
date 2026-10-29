-- 20261030_02_payment_reconcile 当前态校验。
-- 返回 0 行表示迁移完成。

SELECT expected.table_name, NULL AS column_name, 'missing table' AS issue
FROM (
    SELECT 'payment_reconcile_batch' AS table_name
    UNION ALL SELECT 'payment_reconcile_diff'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.TABLES actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
WHERE actual.TABLE_NAME IS NULL;
