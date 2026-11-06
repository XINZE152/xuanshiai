-- Q45 rollback: restore the old 80-only check.
-- Guard: abort before touching the constraint when any 50-point row exists.
DROP PROCEDURE IF EXISTS `_paper_plane_unlock_cost_down_guard`;
DELIMITER //
CREATE PROCEDURE `_paper_plane_unlock_cost_down_guard`()
BEGIN
    IF (SELECT COUNT(*) FROM `paper_plane_profile_unlock` WHERE `points_cost` = 50) > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'cannot rollback Q45: paper_plane_profile_unlock contains points_cost=50 rows';
    END IF;
END//
DELIMITER ;
CALL `_paper_plane_unlock_cost_down_guard`();
DROP PROCEDURE `_paper_plane_unlock_cost_down_guard`;

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
    ADD CONSTRAINT `ck_paper_plane_unlock_cost` CHECK (`points_cost` = 80);
