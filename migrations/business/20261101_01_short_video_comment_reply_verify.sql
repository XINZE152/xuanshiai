-- 20261101_01_short_video_comment_reply (verify)
-- Returns 0 rows when admin_reply exists as nullable varchar(500).
SELECT 'short_video_comment' AS table_name,
       'admin_reply' AS column_name,
       'missing column' AS issue
WHERE NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'short_video_comment'
      AND COLUMN_NAME = 'admin_reply'
)

UNION ALL

SELECT 'short_video_comment',
       'admin_reply',
       CONCAT('unexpected definition: ', COLUMN_TYPE, '/', IS_NULLABLE)
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'short_video_comment'
  AND COLUMN_NAME = 'admin_reply'
  AND (COLUMN_TYPE <> 'varchar(500)' OR IS_NULLABLE <> 'YES');
