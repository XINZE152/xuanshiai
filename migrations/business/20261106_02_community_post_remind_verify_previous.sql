-- Expected before Q74: community_post_remind must not exist.
SELECT COUNT(*) AS table_exists
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'community_post_remind';
