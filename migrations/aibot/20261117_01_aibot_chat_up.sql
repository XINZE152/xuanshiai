-- 恋 AI BOT：专属红娘绑定、主会话与轮次落库
CREATE TABLE IF NOT EXISTS `aibot_user_assignment` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `agent_id` VARCHAR(32) NOT NULL,
  `assigned_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `welcome_seen_at` DATETIME NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_assignment_user` (`user_id`),
  KEY `idx_aibot_assignment_agent` (`agent_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aibot_session` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `agent_id` VARCHAR(32) NOT NULL,
  `mode` VARCHAR(16) NOT NULL DEFAULT 'free',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active',
  `needs_welcome` TINYINT NOT NULL DEFAULT 1,
  `last_intent` VARCHAR(32) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_session_id` (`session_id`),
  KEY `idx_aibot_session_user_status` (`user_id`, `status`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aibot_turn` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64) NOT NULL,
  `turn_id` VARCHAR(64) NOT NULL,
  `role` VARCHAR(16) NOT NULL,
  `content` TEXT NOT NULL,
  `intent` VARCHAR(32) NULL,
  `choices_json` JSON NULL,
  `actions_json` JSON NULL,
  `cards_json` JSON NULL,
  `idempotency_key` VARCHAR(128) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_turn_id` (`turn_id`),
  UNIQUE KEY `uk_aibot_turn_idem` (`session_id`, `idempotency_key`),
  KEY `idx_aibot_turn_session` (`session_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
