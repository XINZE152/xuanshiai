-- 测试种子账号标记：与真实会员同表共存，清理时只动 is_test_seed=1。
-- 幂等：INFORMATION_SCHEMA 检查后动态 ADD COLUMN / ADD KEY。

SET NAMES utf8mb4;

SET @is_test_seed_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'is_test_seed'
);
SET @add_is_test_seed = IF(
    @is_test_seed_exists = 0,
    'ALTER TABLE `users` ADD COLUMN `is_test_seed` tinyint NOT NULL DEFAULT 0 COMMENT ''是否测试种子账号 0否 1是；清理时只删此标记'' AFTER `status`',
    'SELECT 1'
);
PREPARE add_is_test_seed_stmt FROM @add_is_test_seed;
EXECUTE add_is_test_seed_stmt;
DEALLOCATE PREPARE add_is_test_seed_stmt;

SET @idx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_is_test_seed'
);
SET @add_idx = IF(
    @idx_exists = 0,
    'ALTER TABLE `users` ADD KEY `idx_users_is_test_seed` (`is_test_seed`)',
    'SELECT 1'
);
PREPARE add_idx_stmt FROM @add_idx;
EXECUTE add_idx_stmt;
DEALLOCATE PREPARE add_idx_stmt;
