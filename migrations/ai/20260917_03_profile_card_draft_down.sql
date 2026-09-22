-- 20260917_03 回滚：移除资料卡草稿表。
-- 表内只有未对外展示的草稿与采用痕迹，删除不影响 user_profile。

DROP TABLE IF EXISTS `ai_profile_card_draft`;
