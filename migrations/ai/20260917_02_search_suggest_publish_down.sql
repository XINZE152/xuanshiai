-- 20260917_02 回滚：移除建议发布暂存表。
--
-- 该表只是建议结果的暂存与发布证据副本，删除不改变 Redis 缓存内容（缓存
-- 会按自身 TTL 过期）；回退到"handler 直写缓存"的旧实现时本表无人读写。
-- 若需完全恢复旧行为，回滚后另按运维命令清理 `ai:search_suggest:*` 键。

DROP TABLE IF EXISTS `ai_search_suggest_publish`;
