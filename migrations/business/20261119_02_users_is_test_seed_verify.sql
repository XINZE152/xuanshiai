-- 返回 0 行表示迁移完成。

SELECT 'users' AS table_name,
       'is_test_seed' AS column_name,
       'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'is_test_seed'
)

UNION ALL

SELECT 'users',
       'is_test_seed',
       CONCAT('unexpected definition: ', COLUMN_TYPE, '/', IS_NULLABLE, '/', IFNULL(COLUMN_DEFAULT, 'NULL'))
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'users'
  AND COLUMN_NAME = 'is_test_seed'
  AND (
        COLUMN_TYPE NOT LIKE 'tinyint%'
     OR IS_NULLABLE <> 'NO'
     OR IFNULL(COLUMN_DEFAULT, '') <> '0'
  )

UNION ALL

SELECT 'users',
       'idx_users_is_test_seed',
       'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_is_test_seed'
);
