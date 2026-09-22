-- 20260906_01 down: 恢复 64 列宽（仅在无超长 id 行时安全回滚）。

ALTER TABLE `ai_memory_projection`
    MODIFY COLUMN `projection_id` varchar(64) NOT NULL COMMENT '全局唯一投影 ID';

ALTER TABLE `ai_memory_projection_grant`
    MODIFY COLUMN `grant_id` varchar(64) NOT NULL COMMENT '全局唯一授权 ID';
