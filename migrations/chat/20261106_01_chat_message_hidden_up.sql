-- Q63: per-user hidden chat messages. Hidden rows never affect the other participant.
CREATE TABLE IF NOT EXISTS `chat_message_hidden` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `message_id` bigint unsigned NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `hidden_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_chat_message_hidden_message_user` (`message_id`,`user_id`),
    KEY `idx_chat_message_hidden_user` (`user_id`,`hidden_at`),
    CONSTRAINT `fk_chat_message_hidden_message` FOREIGN KEY (`message_id`) REFERENCES `chat_message` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_chat_message_hidden_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户仅自己隐藏的聊天消息';
