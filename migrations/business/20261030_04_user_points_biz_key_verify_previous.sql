-- 20261030_04_user_points_biz_key 回滚后校验。
-- 返回 0 行表示 down 已恢复旧结构。

SELECT 'user_points' AS table_name, 'biz_key' AS column_name, 'column not removed' AS issue
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND COLUMN_NAME = 'biz_key'
)

UNION ALL

SELECT 'user_points', 'uk_user_points_biz_key', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND INDEX_NAME = 'uk_user_points_biz_key'
);
