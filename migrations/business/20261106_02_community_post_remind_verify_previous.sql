-- Q74 (verify_previous): 返回 0 行表示已回滚到升级前状态。
-- 期望：community_post_remind 表不存在。

SELECT 'community_post_remind' AS table_name,
       NULL AS object_name,
       'table not removed' AS issue
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'community_post_remind';
