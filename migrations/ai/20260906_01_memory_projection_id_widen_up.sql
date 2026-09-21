-- 20260906_01: 放宽 ai_memory_projection / ai_memory_projection_grant 的
-- projection_id / grant_id 列宽（64 -> 96）。
--
-- 背景（Phase 3 Task 5 真实 DB 验证暴露）：projection_id 形如
--   prj:{owner}:{function_key}:{purpose}:{data_category}:{version}
-- grant_id 形如
--   prj-grant:{owner}:{function_key}:{purpose}:{data_category}
-- 启用 counselor_context（17 字符）+ ideal_partner_preference（25 字符）组合
-- 后最长可达 84 字符，超出 varchar(64)（写入报 1406 Data too long）。
-- 96 覆盖 bigint owner 上限（20 位）与全部冻结词汇组合并留余量。

ALTER TABLE `ai_memory_projection`
    MODIFY COLUMN `projection_id` varchar(96) NOT NULL COMMENT '全局唯一投影 ID';

ALTER TABLE `ai_memory_projection_grant`
    MODIFY COLUMN `grant_id` varchar(96) NOT NULL COMMENT '全局唯一授权 ID';
