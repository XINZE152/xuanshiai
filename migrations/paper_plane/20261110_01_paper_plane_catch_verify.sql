-- Q69 verify: 返回 0 行表示表、列与唯一键齐全。
SELECT 'paper_plane_catch' AS table_name, 'caught_date' AS object_name, 'missing column' AS issue
FROM (
    SELECT 'user_id' AS column_name
    UNION ALL SELECT 'plane_id'
    UNION ALL SELECT 'caught_date'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = 'paper_plane_catch'
   AND actual.COLUMN_NAME = expected.column_name
WHERE actual.column_name IS NULL

UNION ALL

SELECT 'paper_plane_catch', 'uk_paper_plane_catch_user_plane_date', 'missing unique key'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'paper_plane_catch'
      AND INDEX_NAME = 'uk_paper_plane_catch_user_plane_date'
)

UNION ALL

SELECT 'paper_plane_catch', 'paper_plane_catch', 'missing table'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'paper_plane_catch'
);
