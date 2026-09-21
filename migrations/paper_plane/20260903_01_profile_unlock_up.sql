-- Paper-plane profile unlock entitlement.  Contact fields are intentionally
-- absent; the entitlement only records who paid to view a target profile.
CREATE TABLE IF NOT EXISTS `paper_plane_profile_unlock` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `viewer_user_id` bigint unsigned NOT NULL,
    `target_user_id` bigint unsigned NOT NULL,
    `points_cost` int unsigned NOT NULL DEFAULT 80,
    `unlock_source` varchar(32) NOT NULL DEFAULT 'paper_plane',
    `idempotency_key` varchar(128) NOT NULL,
    `unlocked_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_paper_plane_unlock_viewer_target` (`viewer_user_id`, `target_user_id`),
    UNIQUE KEY `uk_paper_plane_unlock_viewer_key` (`viewer_user_id`, `idempotency_key`),
    KEY `idx_paper_plane_unlock_target` (`target_user_id`),
    CONSTRAINT `fk_paper_plane_unlock_viewer` FOREIGN KEY (`viewer_user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `fk_paper_plane_unlock_target` FOREIGN KEY (`target_user_id`) REFERENCES `users` (`id`) ON DELETE RESTRICT,
    CONSTRAINT `ck_paper_plane_unlock_cost` CHECK (`points_cost` = 80)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='纸飞机目标用户资料解锁（不存联系方式）';
