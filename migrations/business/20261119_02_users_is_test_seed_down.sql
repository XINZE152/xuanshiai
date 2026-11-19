-- 回滚测试种子标记列（会丢失清理依据，仅在确认无种子数据后执行）。

ALTER TABLE `users` DROP KEY `idx_users_is_test_seed`;
ALTER TABLE `users` DROP COLUMN `is_test_seed`;
