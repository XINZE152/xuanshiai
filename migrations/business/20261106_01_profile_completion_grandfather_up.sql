-- Q23: grandfather completion rules for existing rows until the next active edit.
-- Only backfill when the column is first added; reruns must not reset cleared flags.
SET @completion_grandfathered_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_profile_completion'
      AND COLUMN_NAME = 'completion_grandfathered'
);
SET @add_completion_grandfathered = IF(
    @completion_grandfathered_exists = 0,
    'ALTER TABLE `user_profile_completion` ADD COLUMN `completion_grandfathered` tinyint NOT NULL DEFAULT 0 COMMENT ''1=保留旧完整度规则直到下次主动编辑'' AFTER `single_pledge_completed`',
    'SELECT 1'
);
PREPARE add_completion_grandfathered_stmt FROM @add_completion_grandfathered;
EXECUTE add_completion_grandfathered_stmt;
DEALLOCATE PREPARE add_completion_grandfathered_stmt;

SET @backfill_completion_grandfathered = IF(
    @completion_grandfathered_exists = 0,
    'UPDATE `user_profile_completion` SET `completion_grandfathered` = 1',
    'SELECT 1'
);
PREPARE backfill_completion_grandfathered_stmt FROM @backfill_completion_grandfathered;
EXECUTE backfill_completion_grandfathered_stmt;
DEALLOCATE PREPARE backfill_completion_grandfathered_stmt;
