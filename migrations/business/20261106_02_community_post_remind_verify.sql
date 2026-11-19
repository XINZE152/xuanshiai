-- Q74 (verify): 返回 0 行表示迁移完成（runner 的 _verify 只接受单条查询）。
-- 期望：community_post_remind 存在，post_id/user_id 齐全，唯一键与二级索引齐全。

SELECT 'community_post_remind' AS table_name, expected.column_name AS object_name, 'missing column' AS issue
FROM (
    SELECT 'post_id' AS column_name
    UNION ALL SELECT 'user_id'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = 'community_post_remind'
   AND actual.COLUMN_NAME = expected.column_name
WHERE actual.column_name IS NULL

UNION ALL

SELECT 'community_post_remind', expected.index_name, 'missing index' AS issue
FROM (
    SELECT 'uk_community_post_remind_post_user' AS index_name
    UNION ALL SELECT 'idx_community_post_remind_user'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.STATISTICS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = 'community_post_remind'
   AND actual.INDEX_NAME = expected.index_name
WHERE actual.index_name IS NULL

UNION ALL

SELECT 'community_post_remind', 'community_post_remind', 'missing table' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'community_post_remind'
);
