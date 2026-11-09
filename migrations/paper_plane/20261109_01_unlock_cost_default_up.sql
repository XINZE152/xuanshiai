-- Q45 (2026-11-09): align the stored default with the 50-point unlock constant
-- and keep the widened check that allows historical 80-point rows.
-- Idempotent: every DDL is guarded by an information_schema probe.
SET @unlock_cost_default_is_50 = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'paper_plane_profile_unlock'
      AND COLUMN_NAME = 'points_cost'
      AND COLUMN_DEFAULT = '50'
);
SET @set_unlock_cost_default = IF(
    @unlock_cost_default_is_50 = 0,
    'ALTER TABLE `paper_plane_profile_unlock` ALTER COLUMN `points_cost` SET DEFAULT 50',
    'SELECT 1'
);
PREPARE set_unlock_cost_default_stmt FROM @set_unlock_cost_default;
EXECUTE set_unlock_cost_default_stmt;
DEALLOCATE PREPARE set_unlock_cost_default_stmt;

SET @unlock_cost_check_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
      AND tc.TABLE_NAME = 'paper_plane_profile_unlock'
      AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
);
SET @unlock_cost_check_widened = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
      AND tc.TABLE_NAME = 'paper_plane_profile_unlock'
      AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
      AND cc.CHECK_CLAUSE LIKE '%50%'
);
SET @drop_unlock_cost_check = IF(
    @unlock_cost_check_exists > 0 AND @unlock_cost_check_widened = 0,
    'ALTER TABLE `paper_plane_profile_unlock` DROP CHECK `ck_paper_plane_unlock_cost`',
    'SELECT 1'
);
PREPARE drop_unlock_cost_check_stmt FROM @drop_unlock_cost_check;
EXECUTE drop_unlock_cost_check_stmt;
DEALLOCATE PREPARE drop_unlock_cost_check_stmt;

SET @add_widened_unlock_cost_check = IF(
    @unlock_cost_check_widened = 0,
    'ALTER TABLE `paper_plane_profile_unlock` ADD CONSTRAINT `ck_paper_plane_unlock_cost` CHECK (`points_cost` IN (50,80))',
    'SELECT 1'
);
PREPARE add_widened_unlock_cost_check_stmt FROM @add_widened_unlock_cost_check;
EXECUTE add_widened_unlock_cost_check_stmt;
DEALLOCATE PREPARE add_widened_unlock_cost_check_stmt;
