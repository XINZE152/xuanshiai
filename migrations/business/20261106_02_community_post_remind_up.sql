-- Q74: author-selected reminder list for a community post.
CREATE TABLE IF NOT EXISTS `community_post_remind` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `post_id` bigint unsigned NOT NULL,
    `user_id` bigint unsigned NOT NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_community_post_remind_post_user` (`post_id`,`user_id`),
    KEY `idx_community_post_remind_user` (`user_id`,`created_at`),
    CONSTRAINT `fk_community_post_remind_post` FOREIGN KEY (`post_id`) REFERENCES `community_post` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_community_post_remind_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='动态提醒谁看名单';
