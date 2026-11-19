-- 恋 AI BOT：剧本游玩记录 + 同本同好已推去重（真实推荐，非 Demo 假人）
CREATE TABLE IF NOT EXISTS `aibot_story_play` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `task_id` VARCHAR(64) NOT NULL,
  `status` VARCHAR(16) NOT NULL DEFAULT 'playing' COMMENT 'playing|paused|finished',
  `chapter_idx` INT NOT NULL DEFAULT 0,
  `started_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_active_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_story_play_user_task` (`user_id`, `task_id`),
  KEY `idx_aibot_story_play_task_active` (`task_id`, `last_active_at`),
  KEY `idx_aibot_story_play_task_finished` (`task_id`, `finished_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `aibot_story_peer_shown` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `task_id` VARCHAR(64) NOT NULL,
  `peer_user_id` BIGINT UNSIGNED NOT NULL,
  `shown_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_story_peer_shown` (`user_id`, `task_id`, `peer_user_id`),
  KEY `idx_aibot_story_peer_shown_user_task` (`user_id`, `task_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
