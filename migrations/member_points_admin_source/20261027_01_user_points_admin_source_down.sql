-- =============================================================================
-- 后台积分发放/扣减补列迁移（A12-积分修复）（down）
--
-- 回滚 20261027_01_user_points_admin_source_up.sql：删除 user_points.source_type
-- 与 user_points.operator_id 两个可空列。
--
-- 警告：DROP COLUMN 会永久丢弃后台发放/扣减行的来源与操作人标记（可由
-- business_audit_log 反查，但非逐行原值）。可重复执行：列不存在时跳过。
-- =============================================================================

SET NAMES utf8mb4;

SET @up_points_source_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_points' AND COLUMN_NAME = 'source_type'
);
SET @up_points_source_drop = IF(@up_points_source_exists > 0,
    'ALTER TABLE `user_points` DROP COLUMN `source_type`', 'SELECT 1');
PREPARE up_points_source_drop_stmt FROM @up_points_source_drop;
EXECUTE up_points_source_drop_stmt;
DEALLOCATE PREPARE up_points_source_drop_stmt;

SET @up_points_operator_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_points' AND COLUMN_NAME = 'operator_id'
);
SET @up_points_operator_drop = IF(@up_points_operator_exists > 0,
    'ALTER TABLE `user_points` DROP COLUMN `operator_id`', 'SELECT 1');
PREPARE up_points_operator_drop_stmt FROM @up_points_operator_drop;
EXECUTE up_points_operator_drop_stmt;
DEALLOCATE PREPARE up_points_operator_drop_stmt;
