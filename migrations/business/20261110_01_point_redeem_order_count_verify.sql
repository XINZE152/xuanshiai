-- Q66 verify: 返回 0 行表示 point_redeem_order.count 已存在且默认值为 1。

SELECT 'point_redeem_order' AS table_name, 'count' AS object_name, 'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'point_redeem_order'
      AND COLUMN_NAME = 'count'
)

UNION ALL

SELECT 'point_redeem_order', 'count', CONCAT('unexpected default: ', COALESCE(COLUMN_DEFAULT, 'NULL'))
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'point_redeem_order'
  AND COLUMN_NAME = 'count'
  AND COLUMN_DEFAULT <> '1';
