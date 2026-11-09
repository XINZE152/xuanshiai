-- Q23 (2026-11-09): backfill completion rows for legacy users who were already at 100%
-- but never had a user_profile_completion row. Other users are recalculated under profile-v5.
-- Idempotent: only inserts when no completion row exists.
INSERT INTO `user_profile_completion`
    (`user_id`, `score`, `completion_grandfathered`, `algorithm_version`, `calculated_at`)
SELECT u.`id`,
       LEAST(100, GREATEST(0, COALESCE(u.`data_complete_rate`, 0))),
       1,
       'legacy-backfill-20261109',
       UTC_TIMESTAMP()
FROM `users` u
LEFT JOIN `user_profile_completion` c ON c.`user_id` = u.`id`
WHERE c.`user_id` IS NULL
  AND COALESCE(u.`status`, 1) <> 3
  AND COALESCE(u.`data_complete_rate`, 0) >= 100;
