-- 20260917_02 建议缓存发布闭环：新增待发布暂存表
--
-- 背景（修复清单 §3.7 / C-05、C-06）：猜你喜欢 AI 建议原先由 Worker handler
-- 直接写 Redis（`ai:search_suggest:{uid}`，24h TTL），该写入发生在完成期复核
-- 之前，且不受 Worker savepoint 保护——用户在任务执行期间撤回 search_parse
-- 授权时，任务会被判 superseded，但已写入的缓存仍会被 GET 读到，直到 TTL 到期；
-- 同时该 key 不被任何清理 pattern 覆盖，撤回/删除后永不失效。
--
-- 本次把"计算完成"与"对外发布"分离：
--   * handler 只在同事务内写 `ai_search_suggest_publish`（status='staged'）；
--   * Worker 完成期复核（complete_task）通过并 commit 之后才发 Redis，且发布前
--     再核验一次当前 search_parse 授权与五维版本向量；
--   * Redis key 携带代际（`ai:search_suggest:{uid}:{generation}`），读取端先算
--     当前代际再取对应 key，迟到旧任务只会写到当前读不到的 key。
--
-- 本迁移只新增一张表，不改已有列、不删数据。幂等：IF NOT EXISTS。
--
-- 回滚说明：down 直接 DROP 本表。表内只有尚未发布或已发布建议的副本，
-- Redis 缓存会自然过期，删除本表不影响已发布内容的一致性。

CREATE TABLE IF NOT EXISTS `ai_search_suggest_publish` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `user_id` bigint unsigned NOT NULL,
    `task_id` varchar(64) NOT NULL,
    `generation` char(64) NOT NULL COMMENT '发布代际：与读取端当前代际不一致时不得发布/读取',
    `suggestions_json` json NOT NULL COMMENT '已归纳搜索词（≤5 条），不含原文与条目摘要',
    `consent_snapshot_json` json DEFAULT NULL COMMENT '发布时的 search_parse 授权快照',
    `source_revision_json` json DEFAULT NULL COMMENT '发布时的五维版本向量快照',
    `status` varchar(24) NOT NULL DEFAULT 'staged' COMMENT 'staged/published/superseded',
    `staged_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `published_at` datetime DEFAULT NULL,
    `expires_at` datetime NOT NULL COMMENT '发布后 TTL（staged 行由清理按期回收）',
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_search_suggest_publish_task` (`task_id`),
    KEY `idx_ai_search_suggest_publish_user` (`user_id`, `status`),
    KEY `idx_ai_search_suggest_publish_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 猜你喜欢建议的待发布暂存与发布证据';
