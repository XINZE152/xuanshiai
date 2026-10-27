-- 20261027_04_fk_delete_policy
-- P0-2：金额/积分/会员/举报证据禁止级联删除。
-- 幂等：按 information_schema.REFERENTIAL_CONSTRAINTS 判断，已是 RESTRICT 时不重建。
-- 注意：若存量数据存在孤儿行，ADD CONSTRAINT 会失败（1452/1215）。先清理再迁移：
--   SELECT fk.user_id FROM `payment_order` fk LEFT JOIN `users` u ON u.id = fk.user_id WHERE u.id IS NULL LIMIT 20;
-- Run with the intended database selected (MySQL 8+).
SET NAMES utf8mb4;

-- payment_order.user_id -> users.id
SET @fk_fk_payment_order_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order' AND CONSTRAINT_NAME = 'fk_payment_order_user_id'
);
SET @sql = IF(@fk_fk_payment_order_user_id IS NULL OR UPPER(@fk_fk_payment_order_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `payment_order` DROP FOREIGN KEY `fk_payment_order_user_id`');
PREPARE drop_fk_payment_order_user_id FROM @sql;
EXECUTE drop_fk_payment_order_user_id;
DEALLOCATE PREPARE drop_fk_payment_order_user_id;
SET @sql = IF(@fk_fk_payment_order_user_id IS NULL OR UPPER(@fk_fk_payment_order_user_id) <> 'RESTRICT',
              'ALTER TABLE `payment_order` ADD CONSTRAINT `fk_payment_order_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_payment_order_user_id FROM @sql;
EXECUTE add_fk_payment_order_user_id;
DEALLOCATE PREPARE add_fk_payment_order_user_id;

-- user_points.user_id -> users.id
SET @fk_fk_user_points_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_points' AND CONSTRAINT_NAME = 'fk_user_points_user_id'
);
SET @sql = IF(@fk_fk_user_points_user_id IS NULL OR UPPER(@fk_fk_user_points_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `user_points` DROP FOREIGN KEY `fk_user_points_user_id`');
PREPARE drop_fk_user_points_user_id FROM @sql;
EXECUTE drop_fk_user_points_user_id;
DEALLOCATE PREPARE drop_fk_user_points_user_id;
SET @sql = IF(@fk_fk_user_points_user_id IS NULL OR UPPER(@fk_fk_user_points_user_id) <> 'RESTRICT',
              'ALTER TABLE `user_points` ADD CONSTRAINT `fk_user_points_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_user_points_user_id FROM @sql;
EXECUTE add_fk_user_points_user_id;
DEALLOCATE PREPARE add_fk_user_points_user_id;

-- user_membership.user_id -> users.id
SET @fk_fk_user_membership_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership' AND CONSTRAINT_NAME = 'fk_user_membership_user_id'
);
SET @sql = IF(@fk_fk_user_membership_user_id IS NULL OR UPPER(@fk_fk_user_membership_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `user_membership` DROP FOREIGN KEY `fk_user_membership_user_id`');
PREPARE drop_fk_user_membership_user_id FROM @sql;
EXECUTE drop_fk_user_membership_user_id;
DEALLOCATE PREPARE drop_fk_user_membership_user_id;
SET @sql = IF(@fk_fk_user_membership_user_id IS NULL OR UPPER(@fk_fk_user_membership_user_id) <> 'RESTRICT',
              'ALTER TABLE `user_membership` ADD CONSTRAINT `fk_user_membership_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_user_membership_user_id FROM @sql;
EXECUTE add_fk_user_membership_user_id;
DEALLOCATE PREPARE add_fk_user_membership_user_id;

-- user_report.user_id -> users.id
SET @fk_fk_user_report_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_report' AND CONSTRAINT_NAME = 'fk_user_report_user_id'
);
SET @sql = IF(@fk_fk_user_report_user_id IS NULL OR UPPER(@fk_fk_user_report_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `user_report` DROP FOREIGN KEY `fk_user_report_user_id`');
PREPARE drop_fk_user_report_user_id FROM @sql;
EXECUTE drop_fk_user_report_user_id;
DEALLOCATE PREPARE drop_fk_user_report_user_id;
SET @sql = IF(@fk_fk_user_report_user_id IS NULL OR UPPER(@fk_fk_user_report_user_id) <> 'RESTRICT',
              'ALTER TABLE `user_report` ADD CONSTRAINT `fk_user_report_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_user_report_user_id FROM @sql;
EXECUTE add_fk_user_report_user_id;
DEALLOCATE PREPARE add_fk_user_report_user_id;

-- user_report.target_user_id -> users.id
SET @fk_fk_user_report_target_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_report' AND CONSTRAINT_NAME = 'fk_user_report_target_user_id'
);
SET @sql = IF(@fk_fk_user_report_target_user_id IS NULL OR UPPER(@fk_fk_user_report_target_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `user_report` DROP FOREIGN KEY `fk_user_report_target_user_id`');
PREPARE drop_fk_user_report_target_user_id FROM @sql;
EXECUTE drop_fk_user_report_target_user_id;
DEALLOCATE PREPARE drop_fk_user_report_target_user_id;
SET @sql = IF(@fk_fk_user_report_target_user_id IS NULL OR UPPER(@fk_fk_user_report_target_user_id) <> 'RESTRICT',
              'ALTER TABLE `user_report` ADD CONSTRAINT `fk_user_report_target_user_id` FOREIGN KEY (`target_user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_user_report_target_user_id FROM @sql;
EXECUTE add_fk_user_report_target_user_id;
DEALLOCATE PREPARE add_fk_user_report_target_user_id;

-- report_appeal.appellant_user_id -> users.id
SET @fk_fk_report_appeal_appellant_user_id = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'report_appeal' AND CONSTRAINT_NAME = 'fk_report_appeal_appellant_user_id'
);
SET @sql = IF(@fk_fk_report_appeal_appellant_user_id IS NULL OR UPPER(@fk_fk_report_appeal_appellant_user_id) = 'RESTRICT',
              'SELECT 1',
              'ALTER TABLE `report_appeal` DROP FOREIGN KEY `fk_report_appeal_appellant_user_id`');
PREPARE drop_fk_report_appeal_appellant_user_id FROM @sql;
EXECUTE drop_fk_report_appeal_appellant_user_id;
DEALLOCATE PREPARE drop_fk_report_appeal_appellant_user_id;
SET @sql = IF(@fk_fk_report_appeal_appellant_user_id IS NULL OR UPPER(@fk_fk_report_appeal_appellant_user_id) <> 'RESTRICT',
              'ALTER TABLE `report_appeal` ADD CONSTRAINT `fk_report_appeal_appellant_user_id` FOREIGN KEY (`appellant_user_id`) REFERENCES `users`(`id`) ON DELETE RESTRICT',
              'SELECT 1');
PREPARE add_fk_report_appeal_appellant_user_id FROM @sql;
EXECUTE add_fk_report_appeal_appellant_user_id;
DEALLOCATE PREPARE add_fk_report_appeal_appellant_user_id;
