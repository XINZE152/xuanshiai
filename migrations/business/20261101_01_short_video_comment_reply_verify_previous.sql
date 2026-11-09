-- 20261101_01_short_video_comment_reply (verify_previous)
-- Expected: the admin_reply column is absent after rollback.
SELECT 'short_video_comment' AS table_name,
       'admin_reply' AS column_name,
       'column not removed' AS issue
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'short_video_comment'
  AND COLUMN_NAME = 'admin_reply';
