-- Q23 (verify): 返回 0 行表示迁移完成。
-- 期望：user_profile_completion.completion_grandfathered 存在，且为 NOT NULL、默认 0。

SELECT 'user_profile_completion' AS table_name,
       'completion_grandfathered' AS column_name,
       'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_profile_completion'
      AND COLUMN_NAME = 'completion_grandfathered'
)

UNION ALL

SELECT 'user_profile_completion',
       'completion_grandfathered',
       CONCAT('unexpected definition: ', COLUMN_TYPE, '/', IS_NULLABLE, '/', IFNULL(COLUMN_DEFAULT, 'NULL'))
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'user_profile_completion'
  AND COLUMN_NAME = 'completion_grandfathered'
  AND (
        COLUMN_TYPE NOT LIKE 'tinyint%'
     OR IS_NULLABLE <> 'NO'
     OR IFNULL(COLUMN_DEFAULT, '') <> '0'
  );
