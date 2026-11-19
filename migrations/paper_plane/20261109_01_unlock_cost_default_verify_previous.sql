-- Q45 verify_previous: returns 0 rows when the default is back to 80 and the check exists.
SELECT 'paper_plane_profile_unlock' AS table_name,
       'points_cost' AS object_name,
       CONCAT('unexpected default: ', IFNULL(COLUMN_DEFAULT, 'NULL')) AS issue
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'paper_plane_profile_unlock'
  AND COLUMN_NAME = 'points_cost'
  AND IFNULL(COLUMN_DEFAULT, '') <> '80'

UNION ALL

SELECT 'paper_plane_profile_unlock',
       'ck_paper_plane_unlock_cost',
       'missing check constraint'
WHERE NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA
     AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME
    WHERE cc.CONSTRAINT_SCHEMA = DATABASE()
      AND tc.TABLE_NAME = 'paper_plane_profile_unlock'
      AND cc.CONSTRAINT_NAME = 'ck_paper_plane_unlock_cost'
);
