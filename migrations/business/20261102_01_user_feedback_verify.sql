-- 20261102_01_user_feedback (verify)
-- 返回 0 行表示迁移完成（runner 的 _verify 只接受单条查询）。

SELECT expected.table_name, expected.column_name, 'missing column' AS issue
FROM (
    SELECT 'user_feedback' AS table_name, 'user_id' AS column_name
    UNION ALL SELECT 'user_feedback', 'category'
    UNION ALL SELECT 'user_feedback', 'content'
    UNION ALL SELECT 'user_feedback', 'status'
    UNION ALL SELECT 'user_feedback', 'reply_content'
    UNION ALL SELECT 'user_feedback', 'replied_by'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = expected.table_name
   AND actual.COLUMN_NAME = expected.column_name
WHERE actual.column_name IS NULL

UNION ALL

SELECT 'user_feedback', expected.index_name, 'missing index' AS issue
FROM (
    SELECT 'idx_user_feedback_user' AS index_name
    UNION ALL SELECT 'idx_user_feedback_status'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.STATISTICS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = 'user_feedback'
   AND actual.INDEX_NAME = expected.index_name
WHERE actual.index_name IS NULL

UNION ALL

SELECT 'table', 'user_feedback', 'missing table' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_feedback'
);
