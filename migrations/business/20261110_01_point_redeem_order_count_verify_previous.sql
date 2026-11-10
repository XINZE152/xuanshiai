-- Q66 verify_previous: 返回 0 行表示已回滚到升级前状态（count 列不存在）。

SELECT 'point_redeem_order' AS table_name,
       'count' AS object_name,
       'column not removed' AS issue
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'point_redeem_order'
  AND COLUMN_NAME = 'count';
