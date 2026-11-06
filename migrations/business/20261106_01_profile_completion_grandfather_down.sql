-- Q23 rollback: dropping the marker loses grandfather state; all rows return to current rules.
ALTER TABLE `user_profile_completion`
    DROP COLUMN `completion_grandfathered`;
