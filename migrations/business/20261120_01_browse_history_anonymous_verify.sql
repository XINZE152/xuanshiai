-- 返回 0 行表示迁移完成。

SELECT 'user_browse_history' AS table_name,
       'is_anonymous' AS column_name,
       'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_browse_history'
      AND COLUMN_NAME = 'is_anonymous'
)

UNION ALL

SELECT 'user_browse_history',
       'is_anonymous',
       CONCAT('unexpected definition: ', COLUMN_TYPE, '/', IS_NULLABLE, '/', IFNULL(COLUMN_DEFAULT, 'NULL'))
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'user_browse_history'
  AND COLUMN_NAME = 'is_anonymous'
  AND (
        COLUMN_TYPE NOT LIKE 'tinyint%'
     OR IS_NULLABLE <> 'NO'
     OR IFNULL(COLUMN_DEFAULT, '') <> '0'
  )

UNION ALL

SELECT 'user_browse_history',
       'idx_browse_target_anon',
       'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_browse_history'
      AND INDEX_NAME = 'idx_browse_target_anon'
);
