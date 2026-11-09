-- Q23 rollback: remove only rows created by this backfill.
-- Rows edited after the backfill are recalculated to algorithm_version='profile-v5'
-- with completion_grandfathered=0 and are intentionally kept.
DELETE FROM `user_profile_completion`
WHERE `algorithm_version` = 'legacy-backfill-20261109'
  AND `completion_grandfathered` = 1;
