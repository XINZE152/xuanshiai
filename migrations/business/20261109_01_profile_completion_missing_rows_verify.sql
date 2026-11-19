-- Q23 verify: returns 0 rows when no active 100%-profile legacy user lacks a completion row.
SELECT 'users' AS table_name,
       'completion row' AS object_name,
       CONCAT('missing completion row for user ', u.`id`) AS issue
FROM `users` u
LEFT JOIN `user_profile_completion` c ON c.`user_id` = u.`id`
WHERE c.`user_id` IS NULL
  AND COALESCE(u.`status`, 1) <> 3
  AND COALESCE(u.`data_complete_rate`, 0) >= 100
LIMIT 50;
