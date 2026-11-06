-- Expected before Q23: completion_grandfathered must not exist.
SELECT COUNT(*) AS column_exists
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'user_profile_completion'
  AND COLUMN_NAME = 'completion_grandfathered';
