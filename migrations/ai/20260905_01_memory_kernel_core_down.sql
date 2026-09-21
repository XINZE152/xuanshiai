-- 记忆内核核心 v1 回滚：按建表逆序删除七张记忆表。
-- 只删除本迁移新建的记忆内核表，不删除任何既有业务表（旧画像/任务/授权/
-- 投影链路的表全部保留）。事件账本是 append-only 事实源，回滚即放弃全部
-- 记忆数据，属于破坏性操作：仅允许在 test 目标或 AI_MASTER_ENABLED 关闭时执行。

DROP TABLE IF EXISTS `ai_memory_suppression`;
DROP TABLE IF EXISTS `ai_memory_state`;
DROP TABLE IF EXISTS `ai_memory_insight`;
DROP TABLE IF EXISTS `ai_memory_claim`;
DROP TABLE IF EXISTS `ai_memory_observation`;
DROP TABLE IF EXISTS `ai_memory_event`;
DROP TABLE IF EXISTS `ai_memory_owner_sequence`;
