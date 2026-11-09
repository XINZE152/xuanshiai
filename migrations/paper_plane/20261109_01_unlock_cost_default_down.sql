-- Q45 rollback (2026-11-09): restore the legacy default but keep the widened check.
-- Narrowing back to 80-only is unsafe once any 50-point row exists; a full narrowing
-- requires exporting and deleting those rows first. See docs/api/纸飞机资料解锁.md.
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
