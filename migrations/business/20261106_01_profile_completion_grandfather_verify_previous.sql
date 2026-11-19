-- Q23 (verify_previous): 返回 0 行表示已回滚到升级前状态。
-- 期望：completion_grandfathered 列不存在。

SELECT 'user_profile_completion' AS table_name,
       'completion_grandfathered' AS column_name,
       'column not removed' AS issue
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'user_profile_completion'
  AND COLUMN_NAME = 'completion_grandfathered';
