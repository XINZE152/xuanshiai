-- 20261102_01_user_feedback (verify_previous)
-- 返回 0 行表示已回滚到升级前状态（user_feedback 表不存在）。

SELECT 'user_feedback' AS table_name, NULL AS object_name, 'table not removed' AS issue
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_feedback';
