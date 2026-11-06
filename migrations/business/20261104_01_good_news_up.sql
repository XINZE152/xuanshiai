-- 20261104_01_good_news
-- 红娘喜讯：内容主体继续使用 admin_content_item（domain=good_news），
-- 审核/公开/来源/同意/祝福/报喜/审计拆到关系表。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `good_news_meta` (
    `news_id` bigint unsigned NOT NULL COMMENT 'admin_content_item.id',
    `stage` varchar(32) NOT NULL DEFAULT '' COMMENT '牵手成功/恋爱生活/已见父母/已订婚/已领证/已办婚礼/婚后生活',
    `review_status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED',
    `public_status` varchar(16) NOT NULL DEFAULT 'DRAFT' COMMENT 'DRAFT/PUBLISHED/UNPUBLISHED',
    `source_type` varchar(32) DEFAULT NULL COMMENT 'MATCHMAKER_SERVICE/INTRODUCTION/MEETING_REQUEST/MEETING_RECORD/OFFLINE_VIP/SELF_REPORTED/MANUAL',
    `source_id` bigint unsigned DEFAULT NULL,
    `couple_key` varchar(64) DEFAULT NULL COMMENT '双方 user_id 排序后的稳定键',
    `from_user_id` bigint unsigned DEFAULT NULL,
    `to_user_id` bigint unsigned DEFAULT NULL,
    `matchmaker_id` bigint unsigned DEFAULT NULL,
    `organization_id` bigint unsigned DEFAULT NULL,
    `meet_duration_months` int unsigned DEFAULT NULL,
    `display_mode` varchar(16) NOT NULL DEFAULT 'MASKED' COMMENT 'FULL/MASKED/ANONYMOUS',
    `verification_level` varchar(24) NOT NULL DEFAULT 'SELF_REPORTED' COMMENT 'PLATFORM_VERIFIED/MATCHMAKER_FOLLOWUP/SELF_REPORTED',
    `submitted_by_user_id` bigint unsigned DEFAULT NULL,
    `submitted_by_role` varchar(16) NOT NULL DEFAULT 'MATCHMAKER' COMMENT 'MEMBER/MATCHMAKER/ADMIN',
    `blessing_count` int unsigned NOT NULL DEFAULT 0,
    `published_at` datetime DEFAULT NULL,
    `reviewed_by` bigint unsigned DEFAULT NULL,
    `reviewed_at` datetime DEFAULT NULL,
    `reject_reason` varchar(255) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`news_id`),
    KEY `idx_good_news_meta_public` (`public_status`, `review_status`, `stage`, `published_at`),
    KEY `idx_good_news_meta_matchmaker` (`matchmaker_id`, `created_at`),
    KEY `idx_good_news_meta_org` (`organization_id`, `created_at`),
    KEY `idx_good_news_meta_couple` (`couple_key`),
    KEY `idx_good_news_meta_source` (`source_type`, `source_id`),
    CONSTRAINT `fk_good_news_meta_content` FOREIGN KEY (`news_id`) REFERENCES `admin_content_item` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘喜讯审核/公开/来源元数据';

CREATE TABLE IF NOT EXISTS `good_news_consent` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `news_id` bigint unsigned NOT NULL,
    `user_id` bigint unsigned DEFAULT NULL,
    `role` varchar(8) NOT NULL COMMENT 'FROM/TO',
    `scopes_json` json DEFAULT NULL COMMENT '姓名/头像/生活照/正文/服务红娘展示',
    `consent_version` varchar(32) NOT NULL DEFAULT '',
    `consent_source` varchar(24) NOT NULL DEFAULT 'MEMBER' COMMENT 'MEMBER/MATCHMAKER_ATTESTED/ADMIN',
    `consented_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `revoked_at` datetime DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_good_news_consent_role` (`news_id`, `role`),
    KEY `idx_good_news_consent_user` (`user_id`, `revoked_at`),
    CONSTRAINT `fk_good_news_consent_content` FOREIGN KEY (`news_id`) REFERENCES `admin_content_item` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘喜讯双方同意记录';

CREATE TABLE IF NOT EXISTS `good_news_blessing` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `news_id` bigint unsigned NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_good_news_blessing` (`news_id`, `user_id`),
    KEY `idx_good_news_blessing_user` (`user_id`, `created_at`),
    CONSTRAINT `fk_good_news_blessing_content` FOREIGN KEY (`news_id`) REFERENCES `admin_content_item` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘喜讯祝福明细';

CREATE TABLE IF NOT EXISTS `good_news_report` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `news_id` bigint unsigned DEFAULT NULL COMMENT '提交时生成的内容项 ID',
    `submitter_id` bigint unsigned NOT NULL,
    `submitter_role` varchar(16) NOT NULL DEFAULT 'MEMBER' COMMENT 'MEMBER/MATCHMAKER/ADMIN',
    `from_user_id` bigint unsigned DEFAULT NULL,
    `to_user_id` bigint unsigned DEFAULT NULL,
    `service_id` bigint unsigned DEFAULT NULL COMMENT 'matchmaker_service.id',
    `meeting_id` bigint unsigned DEFAULT NULL COMMENT 'meeting_request.id',
    `source_type` varchar(32) DEFAULT NULL,
    `source_id` bigint unsigned DEFAULT NULL,
    `stage` varchar(32) NOT NULL DEFAULT '',
    `partner_name` varchar(64) DEFAULT NULL,
    `meet_duration_months` int unsigned DEFAULT NULL,
    `content` text NOT NULL,
    `images_json` json DEFAULT NULL,
    `consent_snapshot_json` json DEFAULT NULL,
    `status` varchar(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/APPROVED/REJECTED/WITHDRAWN',
    `reject_reason` varchar(255) DEFAULT NULL,
    `idempotency_key` varchar(128) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `reviewed_at` datetime DEFAULT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_good_news_report_idem` (`submitter_id`, `idempotency_key`),
    KEY `idx_good_news_report_status` (`status`, `created_at`),
    KEY `idx_good_news_report_submitter` (`submitter_id`, `created_at`),
    KEY `idx_good_news_report_news` (`news_id`),
    CONSTRAINT `fk_good_news_report_content` FOREIGN KEY (`news_id`) REFERENCES `admin_content_item` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘喜讯报喜提交记录';

CREATE TABLE IF NOT EXISTS `good_news_audit` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `news_id` bigint unsigned DEFAULT NULL,
    `report_id` bigint unsigned DEFAULT NULL,
    `actor_user_id` bigint unsigned DEFAULT NULL,
    `actor_role` varchar(16) NOT NULL DEFAULT 'SYSTEM' COMMENT 'MEMBER/MATCHMAKER/ADMIN/SYSTEM',
    `action` varchar(32) NOT NULL COMMENT 'SUBMIT/REVIEW_APPROVE/REVIEW_REJECT/PUBLISH/UNPUBLISH/CONSENT/REVOKE/DELETE',
    `reason` varchar(255) DEFAULT NULL,
    `before_status` varchar(255) DEFAULT NULL,
    `after_status` varchar(255) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_good_news_audit_news` (`news_id`, `created_at`),
    KEY `idx_good_news_audit_action` (`action`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红娘喜讯操作审计';
