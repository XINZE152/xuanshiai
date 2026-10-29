-- 20261030_04_user_points_biz_key 当前态校验。
-- 返回 0 行表示迁移完成。

SELECT 'user_points' AS table_name, 'biz_key' AS column_name, 'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND COLUMN_NAME = 'biz_key'
)

UNION ALL

SELECT 'user_points', 'uk_user_points_biz_key', 'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_points'
      AND INDEX_NAME = 'uk_user_points_biz_key'
);
