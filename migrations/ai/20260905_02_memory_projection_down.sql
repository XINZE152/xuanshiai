-- 记忆投影 Phase 2 回滚：仅删除本阶段新增的两张表，
-- 按 projection → grant 逆序；绝不触碰 Core v1 记忆表、
-- 旧投影表或任何业务表。

DROP TABLE IF EXISTS `ai_memory_projection`;
DROP TABLE IF EXISTS `ai_memory_projection_grant`;
