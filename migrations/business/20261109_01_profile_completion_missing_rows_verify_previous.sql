-- Q23 verify_previous: returns 0 rows when the backfill rows have been removed.
SELECT 'user_profile_completion' AS table_name,
       'legacy-backfill-20261109' AS object_name,
       CONCAT('backfill row remains for user ', `user_id`) AS issue
FROM `user_profile_completion`
WHERE `algorithm_version` = 'legacy-backfill-20261109'
  AND `completion_grandfathered` = 1
LIMIT 50;
