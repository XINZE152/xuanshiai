-- Q45: new paper-plane profile unlocks cost 50; historical 80 rows stay valid.
-- Idempotent: drop the old check (if present) and re-add the widened one.
SET @unlock_cost_check_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
      AND TABLE_NAME = 'paper_plane_profile_unlock'
);
SET @drop_unlock_cost_check = IF(
    @unlock_cost_check_exists > 0,
    'ALTER TABLE `paper_plane_profile_unlock` DROP CHECK `ck_paper_plane_unlock_cost`',
    'SELECT 1'
);
PREPARE drop_unlock_cost_check_stmt FROM @drop_unlock_cost_check;
EXECUTE drop_unlock_cost_check_stmt;
DEALLOCATE PREPARE drop_unlock_cost_check_stmt;

ALTER TABLE `paper_plane_profile_unlock`
    ADD CONSTRAINT `ck_paper_plane_unlock_cost` CHECK (`points_cost` IN (50,80));
