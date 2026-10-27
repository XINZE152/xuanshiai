-- 20261027_04_fk_delete_policy down：把删除策略恢复为 CASCADE（仅在明确回滚时使用）。
SET NAMES utf8mb4;

SET @fk_fk_payment_order_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order' AND CONSTRAINT_NAME = 'fk_payment_order_user_id'
);
SET @sql = IF(@fk_fk_payment_order_user_id_down IS NOT NULL AND UPPER(@fk_fk_payment_order_user_id_down) = 'RESTRICT',
              'ALTER TABLE `payment_order` DROP FOREIGN KEY `fk_payment_order_user_id`',
              'SELECT 1');
PREPARE drop_fk_payment_order_user_id_down FROM @sql;
EXECUTE drop_fk_payment_order_user_id_down;
DEALLOCATE PREPARE drop_fk_payment_order_user_id_down;
SET @sql = IF(@fk_fk_payment_order_user_id_down IS NOT NULL AND UPPER(@fk_fk_payment_order_user_id_down) = 'RESTRICT',
              'ALTER TABLE `payment_order` ADD CONSTRAINT `fk_payment_order_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_payment_order_user_id_down FROM @sql;
EXECUTE add_fk_payment_order_user_id_down;
DEALLOCATE PREPARE add_fk_payment_order_user_id_down;

SET @fk_fk_user_points_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_points' AND CONSTRAINT_NAME = 'fk_user_points_user_id'
);
SET @sql = IF(@fk_fk_user_points_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_points_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_points` DROP FOREIGN KEY `fk_user_points_user_id`',
              'SELECT 1');
PREPARE drop_fk_user_points_user_id_down FROM @sql;
EXECUTE drop_fk_user_points_user_id_down;
DEALLOCATE PREPARE drop_fk_user_points_user_id_down;
SET @sql = IF(@fk_fk_user_points_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_points_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_points` ADD CONSTRAINT `fk_user_points_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_user_points_user_id_down FROM @sql;
EXECUTE add_fk_user_points_user_id_down;
DEALLOCATE PREPARE add_fk_user_points_user_id_down;

SET @fk_fk_user_membership_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership' AND CONSTRAINT_NAME = 'fk_user_membership_user_id'
);
SET @sql = IF(@fk_fk_user_membership_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_membership_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_membership` DROP FOREIGN KEY `fk_user_membership_user_id`',
              'SELECT 1');
PREPARE drop_fk_user_membership_user_id_down FROM @sql;
EXECUTE drop_fk_user_membership_user_id_down;
DEALLOCATE PREPARE drop_fk_user_membership_user_id_down;
SET @sql = IF(@fk_fk_user_membership_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_membership_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_membership` ADD CONSTRAINT `fk_user_membership_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_user_membership_user_id_down FROM @sql;
EXECUTE add_fk_user_membership_user_id_down;
DEALLOCATE PREPARE add_fk_user_membership_user_id_down;

SET @fk_fk_user_report_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_report' AND CONSTRAINT_NAME = 'fk_user_report_user_id'
);
SET @sql = IF(@fk_fk_user_report_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_report_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_report` DROP FOREIGN KEY `fk_user_report_user_id`',
              'SELECT 1');
PREPARE drop_fk_user_report_user_id_down FROM @sql;
EXECUTE drop_fk_user_report_user_id_down;
DEALLOCATE PREPARE drop_fk_user_report_user_id_down;
SET @sql = IF(@fk_fk_user_report_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_report_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_report` ADD CONSTRAINT `fk_user_report_user_id` FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_user_report_user_id_down FROM @sql;
EXECUTE add_fk_user_report_user_id_down;
DEALLOCATE PREPARE add_fk_user_report_user_id_down;

SET @fk_fk_user_report_target_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'user_report' AND CONSTRAINT_NAME = 'fk_user_report_target_user_id'
);
SET @sql = IF(@fk_fk_user_report_target_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_report_target_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_report` DROP FOREIGN KEY `fk_user_report_target_user_id`',
              'SELECT 1');
PREPARE drop_fk_user_report_target_user_id_down FROM @sql;
EXECUTE drop_fk_user_report_target_user_id_down;
DEALLOCATE PREPARE drop_fk_user_report_target_user_id_down;
SET @sql = IF(@fk_fk_user_report_target_user_id_down IS NOT NULL AND UPPER(@fk_fk_user_report_target_user_id_down) = 'RESTRICT',
              'ALTER TABLE `user_report` ADD CONSTRAINT `fk_user_report_target_user_id` FOREIGN KEY (`target_user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_user_report_target_user_id_down FROM @sql;
EXECUTE add_fk_user_report_target_user_id_down;
DEALLOCATE PREPARE add_fk_user_report_target_user_id_down;

SET @fk_fk_report_appeal_appellant_user_id_down = (
    SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'report_appeal' AND CONSTRAINT_NAME = 'fk_report_appeal_appellant_user_id'
);
SET @sql = IF(@fk_fk_report_appeal_appellant_user_id_down IS NOT NULL AND UPPER(@fk_fk_report_appeal_appellant_user_id_down) = 'RESTRICT',
              'ALTER TABLE `report_appeal` DROP FOREIGN KEY `fk_report_appeal_appellant_user_id`',
              'SELECT 1');
PREPARE drop_fk_report_appeal_appellant_user_id_down FROM @sql;
EXECUTE drop_fk_report_appeal_appellant_user_id_down;
DEALLOCATE PREPARE drop_fk_report_appeal_appellant_user_id_down;
SET @sql = IF(@fk_fk_report_appeal_appellant_user_id_down IS NOT NULL AND UPPER(@fk_fk_report_appeal_appellant_user_id_down) = 'RESTRICT',
              'ALTER TABLE `report_appeal` ADD CONSTRAINT `fk_report_appeal_appellant_user_id` FOREIGN KEY (`appellant_user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE',
              'SELECT 1');
PREPARE add_fk_report_appeal_appellant_user_id_down FROM @sql;
EXECUTE add_fk_report_appeal_appellant_user_id_down;
DEALLOCATE PREPARE add_fk_report_appeal_appellant_user_id_down;
