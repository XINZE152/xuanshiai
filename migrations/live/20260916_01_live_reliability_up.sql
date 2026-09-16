CREATE TABLE IF NOT EXISTS `live_match_result` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `session_id` bigint unsigned NOT NULL,
    `left_user_id` bigint unsigned NOT NULL,
    `right_user_id` bigint unsigned NOT NULL,
    `left_confirmed_at` datetime DEFAULT NULL,
    `right_confirmed_at` datetime DEFAULT NULL,
    `status` varchar(32) NOT NULL DEFAULT 'WAITING_CONFIRMATION',
    `application_ref` varchar(128) DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_live_match_pair` (`session_id`, `left_user_id`, `right_user_id`),
    KEY `idx_live_match_status` (`session_id`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `live_participant_restriction` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `session_id` bigint unsigned NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `restriction_type` varchar(24) NOT NULL,
    `status` tinyint NOT NULL DEFAULT 1,
    `reason` varchar(255) NOT NULL,
    `created_by` bigint unsigned NOT NULL,
    `ended_by` bigint unsigned DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `ended_at` datetime DEFAULT NULL,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_live_participant_restriction` (`session_id`, `user_id`, `restriction_type`),
    KEY `idx_live_participant_restriction_active` (`session_id`, `status`, `restriction_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `live_outbox_event` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `event_id` char(32) NOT NULL,
    `session_id` bigint unsigned NOT NULL,
    `event_type` varchar(64) NOT NULL,
    `state_version` int unsigned NOT NULL,
    `payload_json` json NOT NULL,
    `status` varchar(24) NOT NULL DEFAULT 'PENDING',
    `publish_attempts` int unsigned NOT NULL DEFAULT 0,
    `next_attempt_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `last_error_code` varchar(64) DEFAULT NULL,
    `occurred_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `published_at` datetime DEFAULT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_live_outbox_event_id` (`event_id`),
    KEY `idx_live_outbox_pending` (`status`, `next_attempt_at`, `id`),
    KEY `idx_live_outbox_session` (`session_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE `live_stage_seat`
    ADD COLUMN `active_user_id` bigint unsigned GENERATED ALWAYS AS
        (CASE WHEN `status` IN ('INVITED','ON_STAGE') THEN `user_id` ELSE NULL END) STORED,
    ADD UNIQUE KEY `uk_live_active_user` (`session_id`, `active_user_id`);

ALTER TABLE `live_provider_event`
    ADD COLUMN `event_time` datetime DEFAULT NULL,
    ADD COLUMN `process_attempts` int unsigned NOT NULL DEFAULT 0,
    ADD COLUMN `last_error_code` varchar(64) DEFAULT NULL;
