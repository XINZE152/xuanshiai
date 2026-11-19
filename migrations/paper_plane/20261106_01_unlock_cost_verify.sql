-- Expected: Q45 widened cost check allows 50 (new) and 80 (historical).
-- Returns 0 rows when the constraint exists, allows both prices, and rows are in range.
SELECT 'paper_plane_profile_unlock' AS table_name,
       'ck_paper_plane_unlock_cost' AS object_name,
       'missing check constraint' AS issue
WHERE NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
      AND tc.TABLE_NAME = 'paper_plane_profile_unlock'
      AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
)

UNION ALL

SELECT 'paper_plane_profile_unlock',
       'ck_paper_plane_unlock_cost',
       CONCAT('check clause does not allow 50/80: ', cc.CHECK_CLAUSE)
FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
  ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
 AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
  AND tc.TABLE_NAME = 'paper_plane_profile_unlock'
  AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
  AND (cc.CHECK_CLAUSE NOT LIKE '%50%' OR cc.CHECK_CLAUSE NOT LIKE '%80%')

UNION ALL

SELECT 'paper_plane_profile_unlock',
       'points_cost',
       CONCAT('unexpected points_cost: ', points_cost)
FROM `paper_plane_profile_unlock`
WHERE points_cost NOT IN (50, 80)
LIMIT 50;
