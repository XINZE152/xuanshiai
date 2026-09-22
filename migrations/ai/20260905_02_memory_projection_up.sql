-- 记忆投影 Phase 2（Memory Projection Phase 2）最小迁移。
-- ai_memory_projection_grant 是 function × purpose × data_category 维度的
-- 读取授权（绑定 profile_text_extract 授权快照与策略版本）；
-- ai_memory_projection 是由 confirmed Claim 构建的版本化最小事实文档，
-- 是下游唯一的记忆读取入口：entries_json 只允许 10 字段 allowlist 的
-- 最小 JSON，transcript / source_quote / 未授权字段禁止进入任何列。
-- 冻结契约见 app/schemas/ai_memory_projection.py 与
-- docs/architecture/ai-memory-projection-phase2.md。

-- 1) 投影授权：owner+function+purpose+category 唯一；revoke 置
--    status='revoked' 并写 revoked_at，立即失效全部关联 active 投影。
CREATE TABLE IF NOT EXISTS `ai_memory_projection_grant` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `grant_id` varchar(64) NOT NULL COMMENT '全局唯一授权 ID',
    `owner_user_id` bigint unsigned NOT NULL,
    `function_key` varchar(32) NOT NULL COMMENT 'search/compatibility/recommend（future key 不予 grant）',
    `purpose` varchar(32) NOT NULL COMMENT 'candidate_filter/candidate_rank/explanation/session_context',
    `data_category` varchar(64) NOT NULL COMMENT 'personal_profile/ideal_partner_preference/compatibility_features/public_profile_summary',
    `status` varchar(24) NOT NULL DEFAULT 'active' COMMENT 'active/revoked',
    `consent_snapshot_id` varchar(128) NOT NULL COMMENT '绑定的 profile_text_extract 授权快照',
    `policy_revision` varchar(64) NOT NULL COMMENT '授权时的策略版本，读取时必须与当前一致',
    `granted_at` datetime NOT NULL,
    `revoked_at` datetime DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_projection_grant_id` (`grant_id`),
    UNIQUE KEY `uk_ai_memory_projection_grant_tuple` (`owner_user_id`, `function_key`, `purpose`, `data_category`),
    KEY `idx_ai_memory_projection_grant_owner_status` (`owner_user_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆投影读取授权';

-- 2) 投影文档：每 owner+function+purpose+category 按 projection_version
--    单调递增、只插入不覆盖；同 input_hash 幂等不重复建版本；
--    失效置 status='invalidated' + invalidated_at（payload 不动）。
CREATE TABLE IF NOT EXISTS `ai_memory_projection` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `projection_id` varchar(64) NOT NULL COMMENT '全局唯一投影 ID',
    `owner_user_id` bigint unsigned NOT NULL,
    `function_key` varchar(32) NOT NULL,
    `purpose` varchar(32) NOT NULL,
    `data_category` varchar(64) NOT NULL,
    `subject` varchar(32) NOT NULL COMMENT 'personal/ideal_partner，与 data_category 互锁',
    `projection_version` bigint unsigned NOT NULL COMMENT '维度内单调递增版本号',
    `projection_input_hash` char(64) NOT NULL COMMENT '构建输入规范指纹（sha256，内容变化即变）',
    `status` varchar(24) NOT NULL DEFAULT 'active' COMMENT 'active/invalidated',
    `invalidated_at` datetime DEFAULT NULL,
    `invalidated_reason` varchar(200) DEFAULT NULL,
    `entries_json` json NOT NULL COMMENT '10 字段 allowlist 最小事实文档；禁止原文摘录',
    `policy_revision` varchar(64) NOT NULL COMMENT '构建时的策略版本，读取时必须与当前一致',
    `consent_snapshot_id` varchar(128) NOT NULL COMMENT '构建时绑定的授权快照',
    `built_at` datetime NOT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_ai_memory_projection_id` (`projection_id`),
    UNIQUE KEY `uk_ai_memory_projection_version` (`owner_user_id`, `function_key`, `purpose`, `data_category`, `projection_version`),
    UNIQUE KEY `uk_ai_memory_projection_input` (`owner_user_id`, `function_key`, `purpose`, `data_category`, `projection_input_hash`),
    KEY `idx_ai_memory_projection_owner_status` (`owner_user_id`, `function_key`, `purpose`, `data_category`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='记忆投影读取视图（confirmed Claim 派生）';
