-- 20260917_04 回滚：移除 user_profile.qa_answers。
-- 仅删除本功能新增列，不改其他资料字段。

ALTER TABLE `user_profile` DROP COLUMN `qa_answers`;
