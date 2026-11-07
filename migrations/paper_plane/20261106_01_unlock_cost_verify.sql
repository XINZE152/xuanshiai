-- Expected: Q45 widened cost check allows 50 (new) and 80 (historical).
SELECT CONSTRAINT_NAME, CHECK_CLAUSE
FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = DATABASE()
  AND CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
  AND TABLE_NAME = 'paper_plane_profile_unlock';

SELECT SUM(points_cost = 50) AS rows_at_50, COUNT(*) AS rows_total
FROM `paper_plane_profile_unlock`;
