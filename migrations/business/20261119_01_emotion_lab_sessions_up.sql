-- 情感实验室 MBTI 真实会话与阶段结果。
-- payload 保存服务端题目/计分快照和当前答案，客户端刷新后可以继续；
-- result 保存每完成 10 题生成的阶段结果，完整 30 题后 status 才变为 completed。
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `emotion_lab_session` (
    `id` char(36) NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `assessment_id` varchar(64) NOT NULL,
    `assessment_version` varchar(64) NOT NULL,
    `result_copy_version` varchar(64) NOT NULL,
    `status` varchar(16) NOT NULL DEFAULT 'in_progress',
    `payload` json NOT NULL,
    `result` json DEFAULT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `completed_at` datetime DEFAULT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_emotion_lab_user_status` (`user_id`, `status`, `updated_at`),
    CONSTRAINT `fk_emotion_lab_session_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='情感实验室 MBTI 测试会话与阶段结果';
