-- Q45 rollback (2026-11-06 migration): restore the legacy default and keep the check executable.
-- The up migration only widened the check; narrowing back to 80-only is unsafe once any
-- 50-point row exists. A full narrowing requires exporting and deleting those rows first;
-- see docs/api/纸飞机资料解锁.md.
SET @unlock_cost_default_is_80 = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'paper_plane_profile_unlock'
      AND COLUMN_NAME = 'points_cost'
      AND COLUMN_DEFAULT = '80'
);
SET @set_unlock_cost_default_80 = IF(
    @unlock_cost_default_is_80 = 0,
    'ALTER TABLE `paper_plane_profile_unlock` ALTER COLUMN `points_cost` SET DEFAULT 80',
    'SELECT 1'
);
PREPARE set_unlock_cost_default_80_stmt FROM @set_unlock_cost_default_80;
EXECUTE set_unlock_cost_default_80_stmt;
DEALLOCATE PREPARE set_unlock_cost_default_80_stmt;

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
SET @restore_widened_unlock_cost_check = IF(
    @unlock_cost_check_exists = 0,
    'ALTER TABLE `paper_plane_profile_unlock` ADD CONSTRAINT `ck_paper_plane_unlock_cost` CHECK (`points_cost` IN (50,80))',
    'SELECT 1'
);
PREPARE restore_widened_unlock_cost_check_stmt FROM @restore_widened_unlock_cost_check;
EXECUTE restore_widened_unlock_cost_check_stmt;
DEALLOCATE PREPARE restore_widened_unlock_cost_check_stmt;
