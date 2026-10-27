-- =============================================================================
-- 后台积分发放/扣减迁到 user_points 所需的补列迁移（A12-积分修复）（up）
--
-- 背景：后台「发放积分 / 扣减积分」(credit-grants / credit-deductions) 此前写
-- account_ledger，导致会员列表「余额」列被虚增而「积分」列恒为 0（A12 记录的后端 BUG）。
-- 修复后发放/扣减改写 C 端积分账本 user_points（滚动余额），为在后台「积分明细」页
-- 保留「来源 / 操作人」两列，给 user_points 增加两个可空列：
--   - `source_type` varchar(32) NULL  ：流水来源标签，后台发放/扣减写 'admin_grant'；
--                                       C 端既有行（签到/任务/邀请/兑换）保持 NULL。
--   - `operator_id` bigint unsigned NULL：后台操作人 users.id；非人工操作为 NULL。
--
-- 可重复执行：列已存在时跳过。向后兼容：均为可空列，存量行自动落 NULL。
-- =============================================================================

SET NAMES utf8mb4;

SET @up_points_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'source_type' AS column_name,
               '`source_type` varchar(32) NULL COMMENT ''流水来源标签，后台发放/扣减=admin_grant（A12-积分修复）''' AS column_ddl
        UNION ALL
        SELECT 2 AS ord, 'operator_id' AS column_name,
               '`operator_id` bigint unsigned NULL COMMENT ''后台操作人 users.id；非人工操作为 NULL（A12-积分修复）''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_points'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_points' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @up_points_ddl = IF(@up_points_missing IS NULL, 'SELECT 1',
    CONCAT('ALTER TABLE `user_points` ', @up_points_missing));
PREPARE up_points_stmt FROM @up_points_ddl;
EXECUTE up_points_stmt;
DEALLOCATE PREPARE up_points_stmt;
