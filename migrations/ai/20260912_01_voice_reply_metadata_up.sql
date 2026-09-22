-- 20260912_01 实时语音 v2：墨相师回复元数据
-- 方案 §3：ai_profile_turn 增加可空 voice_reply_metadata JSON 字段，
-- 记录关联用户轮次、生成 ID、生成状态、播放状态与已播放时长。
-- 旧数据保持 NULL，不推断为已经播放。加法迁移，向后兼容。

ALTER TABLE `ai_profile_turn`
    ADD COLUMN `voice_reply_metadata` json DEFAULT NULL
    COMMENT '实时语音 v2 回复元数据（生成/播放状态），非语音行为 NULL'
    AFTER `source_type`;
