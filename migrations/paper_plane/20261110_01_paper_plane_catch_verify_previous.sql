-- Q69 verify_previous: 返回 0 行表示已回滚（表不存在）。
SELECT 'paper_plane_catch' AS table_name,
       NULL AS object_name,
       'table not removed' AS issue
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'paper_plane_catch';
