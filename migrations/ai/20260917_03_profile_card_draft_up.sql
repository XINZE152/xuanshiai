-- 20260917_03 成稿反哺资料卡草稿：独立草稿表，不改 user_profile 事实列。
--
-- 背景：personal 画像已发布且叙事 confirmed 后，用户可异步生成资料卡开放文本
-- 草稿（self_intro / qa_answers / 兴趣候选）。草稿确认前不得写 user_profile。
-- 本表与墨相 ai_profile_draft 隔离，禁止命名为 portrait_result / profile_ai_draft。
--
-- 幂等：CREATE TABLE IF NOT EXISTS。回滚 down 直接 DROP 本表。

CREATE TABLE IF NOT EXISTS `ai_profile_card_draft` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `draft_id` varchar(64) NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `task_id` varchar(64) DEFAULT NULL,
    `source_revision_id` bigint unsigned DEFAULT NULL,
    `status` varchar(24) NOT NULL DEFAULT 'queued' COMMENT 'queued/running/ready/partial/applied/discarded/failed',
    `expected_revision` int unsigned NOT NULL DEFAULT '1' COMMENT '乐观锁，单调递增',
    `fields_json` json DEFAULT NULL COMMENT '受控草稿字段，不含事实栏与原文 prompt',
    `prompt_version` varchar(32) DEFAULT NULL,
    `schema_version` varchar(32) NOT NULL DEFAULT 'profile-card-summarize-v1',
    `model_name` varchar(64) DEFAULT NULL,
    `token_cost` decimal(10,6) DEFAULT NULL,
    `applied_meta` json DEFAULT NULL COMMENT '采用痕迹：AI 生成 / 经用户修改，不 ALTER user_profile',
    `last_operation_idempotency_key` varchar(128) DEFAULT NULL,
    `last_operation_request_digest` char(64) DEFAULT NULL,
    `last_operation_response_json` json DEFAULT NULL,
    `generated_at` datetime DEFAULT NULL,
    `applied_at` datetime DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_profile_card_draft_id` (`draft_id`),
    KEY `idx_ai_profile_card_draft_user_status` (`user_id`, `status`, `updated_at`),
    KEY `idx_ai_profile_card_draft_task` (`task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='成稿反哺资料卡开放文本草稿';
