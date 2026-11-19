-- 恋 AI BOT：完整聊天时间线（可上翻分页；富消息 payload 原样存）
CREATE TABLE IF NOT EXISTS `aibot_timeline_message` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `message_id` VARCHAR(64) NOT NULL,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `agent_id` VARCHAR(32) NOT NULL,
  `session_id` VARCHAR(64) NULL,
  `role` VARCHAR(16) NOT NULL,
  `kind` VARCHAR(32) NOT NULL DEFAULT 'text',
  `content` TEXT NOT NULL,
  `payload_json` JSON NULL,
  `client_msg_id` VARCHAR(128) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_timeline_msg` (`message_id`),
  UNIQUE KEY `uk_aibot_timeline_client` (`user_id`, `agent_id`, `client_msg_id`),
  KEY `idx_aibot_timeline_user_agent_id` (`user_id`, `agent_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
