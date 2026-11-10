-- Q69 (2026-11-10): 纸飞机「已捡」服务端记录。
-- 幂等约定：CREATE TABLE IF NOT EXISTS；重复执行不改数据。
CREATE TABLE IF NOT EXISTS `paper_plane_catch` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `user_id` bigint unsigned NOT NULL COMMENT '捡取人',
    `plane_id` bigint unsigned NOT NULL COMMENT '纸飞机',
    `caught_date` date NOT NULL COMMENT 'UTC 日期',
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_paper_plane_catch_user_plane_date` (`user_id`,`plane_id`,`caught_date`),
    KEY `idx_paper_plane_catch_user_date` (`user_id`,`caught_date`),
    CONSTRAINT `fk_paper_plane_catch_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
    CONSTRAINT `fk_paper_plane_catch_plane` FOREIGN KEY (`plane_id`) REFERENCES `paper_plane` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='纸飞机捡取记录（按 UTC 日期去重）';
