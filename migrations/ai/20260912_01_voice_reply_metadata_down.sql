-- 20260912_01 实时语音 v2：回滚墨相师回复元数据列。
-- 关闭开关后保留新增记录和字段（方案 §5）；本回滚仅用于完整降级。

ALTER TABLE `ai_profile_turn`
    DROP COLUMN `voice_reply_metadata`;
