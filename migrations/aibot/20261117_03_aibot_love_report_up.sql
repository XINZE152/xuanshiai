-- 恋 AI BOT：恋爱报告多版本存档（支持随时回看 / 下载历史版本）
CREATE TABLE IF NOT EXISTS `aibot_love_report` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `version` INT UNSIGNED NOT NULL,
  `agent_id` VARCHAR(32) NOT NULL,
  -- ai = 模型生成；rule = 模型不可用时的规则版（报告不缺席）
  `scene` VARCHAR(16) NOT NULL DEFAULT 'ai',
  `score_total` INT NOT NULL DEFAULT 0,
  `love_index` DECIMAL(5,2) NOT NULL DEFAULT 0,
  `emotion_label` VARCHAR(32) NULL,
  `emotion_score` DECIMAL(5,2) NULL,
  `payload_json` JSON NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_aibot_report_user_version` (`user_id`, `version`),
  KEY `idx_aibot_report_user_created` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
