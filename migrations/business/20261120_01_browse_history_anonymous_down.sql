-- 回滚浏览足迹无痕标记列。

ALTER TABLE `user_browse_history` DROP KEY `idx_browse_target_anon`;
ALTER TABLE `user_browse_history` DROP COLUMN `is_anonymous`;
