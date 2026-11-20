-- 浏览足迹增加无痕标记：隐身浏览仍写入自己的「我看过谁」，但不进入对方「谁看过我」。
-- 幂等：INFORMATION_SCHEMA 检查后动态 ADD COLUMN。

SET NAMES utf8mb4;

SET @col_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_browse_history'
      AND COLUMN_NAME = 'is_anonymous'
);
SET @add_col = IF(
    @col_exists = 0,
    'ALTER TABLE `user_browse_history` ADD COLUMN `is_anonymous` tinyint NOT NULL DEFAULT 0 COMMENT ''无痕浏览：1=仅记自己足迹，不进对方访客'' AFTER `target_user_id`',
    'SELECT 1'
);
PREPARE add_col_stmt FROM @add_col;
EXECUTE add_col_stmt;
DEALLOCATE PREPARE add_col_stmt;

SET @idx_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_browse_history'
      AND INDEX_NAME = 'idx_browse_target_anon'
);
SET @add_idx = IF(
    @idx_exists = 0,
    'ALTER TABLE `user_browse_history` ADD KEY `idx_browse_target_anon` (`target_user_id`, `is_anonymous`)',
    'SELECT 1'
);
PREPARE add_idx_stmt FROM @add_idx;
EXECUTE add_idx_stmt;
DEALLOCATE PREPARE add_idx_stmt;
