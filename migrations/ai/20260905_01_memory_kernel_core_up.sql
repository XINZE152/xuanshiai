-- 记忆内核核心 v1（Memory Kernel Core v1）最小迁移。
-- ai_memory_event 是 append-only 事实源；observation / claim / insight /
-- state / suppression 是由事件物化的当前视图（视图行允许随事件演进，
-- 事件行永不原地覆盖）。完整对话原文禁止进入 payload_json、source_quote
-- 或任何列；只保存最小摘录与来源指针。
-- 冻结契约见 app/schemas/ai_memory.py 与 docs/architecture/ai-memory-kernel-core-v1.md。

-- 1) owner 序列锁点：物化事务先 SELECT ... FOR UPDATE 本行，再分配
--    连续 server_seq，保证同 owner 并发写入序列严格递增且不重复。
CREATE TABLE IF NOT EXISTS `ai_memory_owner_sequence` (
    `owner_user_id` bigint unsigned NOT NULL,
    `next_seq` bigint unsigned NOT NULL DEFAULT 1 COMMENT '下一个待分配的 server_seq',
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`owner_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 owner 序列锁点';

-- 2) append-only 事件账本（事实源）。
CREATE TABLE IF NOT EXISTS `ai_memory_event` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `event_id` varchar(64) NOT NULL COMMENT '全局唯一事件 ID',
    `owner_user_id` bigint unsigned NOT NULL,
    `server_seq` bigint unsigned NOT NULL COMMENT 'owner 内单调连续序号',
    `subject` varchar(32) NOT NULL COMMENT 'personal/ideal_partner',
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang' COMMENT '本期仅 moxiang',
    `node_type` varchar(32) NOT NULL COMMENT 'observation/claim/insight/state/suppression',
    `event_type` varchar(64) NOT NULL COMMENT '冻结事件类型，见 app/schemas/ai_memory.py',
    `payload_json` json NOT NULL COMMENT '最小 payload；完整对话原文禁止进入',
    `source_kind` varchar(32) NOT NULL COMMENT 'user_explicit/user_confirmed/user_feedback/inferred/behavior',
    `source_turn_id` varchar(64) DEFAULT NULL COMMENT '最小来源指针',
    `source_ref` varchar(256) DEFAULT NULL COMMENT '最小来源指针（候选/turn 引用）',
    `source_quote` varchar(512) DEFAULT NULL COMMENT '最小原文摘录，≤512 字符',
    `causal_event_ids_json` json NOT NULL COMMENT '因果事件链：纠正/替换/删除/解除抑制必须携带',
    `consent_scope` varchar(64) NOT NULL DEFAULT 'profile_text_extract',
    `idempotency_key` varchar(128) NOT NULL COMMENT 'owner 内幂等键，同键回放不重复入账',
    `occurred_at` datetime NOT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_event_event_id` (`event_id`),
    UNIQUE KEY `uk_ai_memory_event_owner_seq` (`owner_user_id`, `server_seq`),
    UNIQUE KEY `uk_ai_memory_event_owner_idem` (`owner_user_id`, `idempotency_key`),
    KEY `idx_ai_memory_event_owner_node` (`owner_user_id`, `subject`, `node_type`, `server_seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核事件账本（append-only 事实源）';

-- 3) Observation 视图：一次有来源的观察/证据。同一 canonical key 允许多条
--    证据行（不做唯一），归并由 Claim 视图承担。
CREATE TABLE IF NOT EXISTS `ai_memory_observation` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `observation_id` varchar(64) NOT NULL,
    `owner_user_id` bigint unsigned NOT NULL,
    `subject` varchar(32) NOT NULL,
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang',
    `canonical_key` varchar(160) NOT NULL,
    `dimension` varchar(64) DEFAULT NULL COMMENT '六维之一，可空',
    `value_json` json DEFAULT NULL,
    `confidence` decimal(5,4) NOT NULL,
    `fact_kind` varchar(32) NOT NULL COMMENT 'about_user/partner_preference，与 subject 互锁',
    `status` varchar(24) NOT NULL DEFAULT 'proposed' COMMENT 'proposed/active/superseded/contradicted/user_corrected/expired',
    `source_kind` varchar(32) NOT NULL,
    `source_quote` varchar(512) DEFAULT NULL,
    `last_event_id` varchar(64) NOT NULL COMMENT '最后一次作用于本行的事件',
    `last_event_seq` bigint unsigned NOT NULL DEFAULT 0 COMMENT '最后一次作用事件的 server_seq',
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_observation_id` (`observation_id`),
    KEY `idx_ai_memory_observation_canonical` (`owner_user_id`, `subject`, `namespace`, `canonical_key`),
    KEY `idx_ai_memory_observation_owner_status` (`owner_user_id`, `subject`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 Observation 视图（观察/证据）';

-- 4) Claim 视图：同一事实的规范化归并。owner+subject+namespace+canonical_key
--    唯一：每个事实只有一行"当前 Claim"，历史全部留在事件账本。
CREATE TABLE IF NOT EXISTS `ai_memory_claim` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `claim_id` varchar(64) NOT NULL,
    `owner_user_id` bigint unsigned NOT NULL,
    `subject` varchar(32) NOT NULL,
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang',
    `canonical_key` varchar(160) NOT NULL,
    `dimension` varchar(64) DEFAULT NULL COMMENT '六维之一，可空',
    `value_json` json DEFAULT NULL,
    `confidence` decimal(5,4) DEFAULT NULL,
    `stability` decimal(5,4) NOT NULL DEFAULT 0.0000,
    `importance` decimal(5,4) NOT NULL DEFAULT 0.5000,
    `constraint_type` varchar(32) DEFAULT NULL COMMENT '硬约束/偏好类型，仅用户确认时可设定',
    `importance_confirmed` tinyint(1) NOT NULL DEFAULT 0 COMMENT '仅用户确认动作可置 1，AI 推荐值不得设置',
    `fact_kind` varchar(32) NOT NULL COMMENT 'about_user/partner_preference，与 subject 互锁',
    `status` varchar(24) NOT NULL DEFAULT 'proposed' COMMENT 'proposed/confirmed/superseded/contradicted/user_corrected/expired',
    `source_kind` varchar(32) NOT NULL,
    `last_event_id` varchar(64) NOT NULL,
    `last_event_seq` bigint unsigned NOT NULL DEFAULT 0,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_claim_id` (`claim_id`),
    UNIQUE KEY `uk_ai_memory_claim_canonical` (`owner_user_id`, `subject`, `namespace`, `canonical_key`),
    KEY `idx_ai_memory_claim_owner_status` (`owner_user_id`, `subject`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 Claim 视图（规范化归并）';

-- 5) Insight 视图：从 Claim 派生，只存摘要与 Claim ids。
CREATE TABLE IF NOT EXISTS `ai_memory_insight` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `insight_id` varchar(64) NOT NULL,
    `owner_user_id` bigint unsigned NOT NULL,
    `subject` varchar(32) NOT NULL,
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang',
    `summary` varchar(200) NOT NULL,
    `claim_ids_json` json NOT NULL COMMENT '派生自哪些 Claim；依赖被纠正时整体 invalidated',
    `confidence` decimal(5,4) NOT NULL DEFAULT 0.5000,
    `status` varchar(24) NOT NULL DEFAULT 'proposed' COMMENT 'proposed/confirmed/invalidated/superseded',
    `last_event_id` varchar(64) NOT NULL,
    `last_event_seq` bigint unsigned NOT NULL DEFAULT 0,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_insight_id` (`insight_id`),
    KEY `idx_ai_memory_insight_owner_status` (`owner_user_id`, `subject`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 Insight 视图（Claim 派生洞察）';

-- 6) State 视图：会话上下文，必须携带 TTL；绝不进入长期画像或匹配。
CREATE TABLE IF NOT EXISTS `ai_memory_state` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `state_id` varchar(64) NOT NULL,
    `owner_user_id` bigint unsigned NOT NULL,
    `subject` varchar(32) NOT NULL,
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang',
    `canonical_key` varchar(160) NOT NULL,
    `value_json` json DEFAULT NULL,
    `valid_until` datetime NOT NULL COMMENT 'TTL 到期后只能转 expired（强制）',
    `confidence` decimal(5,4) DEFAULT NULL,
    `status` varchar(24) NOT NULL DEFAULT 'active' COMMENT 'active/expired/user_deleted',
    `last_event_id` varchar(64) NOT NULL,
    `last_event_seq` bigint unsigned NOT NULL DEFAULT 0,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_state_id` (`state_id`),
    KEY `idx_ai_memory_state_owner_status` (`owner_user_id`, `subject`, `status`),
    KEY `idx_ai_memory_state_expiry` (`valid_until`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 State 视图（带 TTL 会话上下文）';

-- 7) Suppression 视图：用户删除墓碑，阻止同 canonical key 自动重抽取。
CREATE TABLE IF NOT EXISTS `ai_memory_suppression` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `suppression_id` varchar(64) NOT NULL,
    `owner_user_id` bigint unsigned NOT NULL,
    `subject` varchar(32) NOT NULL,
    `namespace` varchar(32) NOT NULL DEFAULT 'moxiang',
    `canonical_key` varchar(160) NOT NULL,
    `reason` varchar(200) DEFAULT NULL,
    `status` varchar(24) NOT NULL DEFAULT 'active' COMMENT 'active/lifted',
    `last_event_id` varchar(64) NOT NULL,
    `last_event_seq` bigint unsigned NOT NULL DEFAULT 0,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_suppression_id` (`suppression_id`),
    UNIQUE KEY `uk_ai_memory_suppression_canonical` (`owner_user_id`, `subject`, `namespace`, `canonical_key`),
    KEY `idx_ai_memory_suppression_owner_status` (`owner_user_id`, `subject`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆内核 Suppression 视图（删除墓碑）';
