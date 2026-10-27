SELECT expected.table_name, expected.column_name
FROM (
    SELECT 'users' AS table_name, 'password_algo' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'password_updated_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'password_failed_count' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'password_locked_until' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'phone_verified_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'wechat_bound_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'last_login_ip' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'last_login_device_id' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'risk_status' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'frozen_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'frozen_reason' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'deletion_requested_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'deletion_scheduled_at' AS column_name
    UNION ALL
    SELECT 'users' AS table_name, 'deletion_cancelled_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'realname_provider' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'provider_request_id' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'provider_result_code' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'submitted_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'verified_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'failed_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'retry_count' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'next_retry_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'manual_review_by' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'manual_review_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'revoked_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'revoked_reason' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'id_card_masked' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'encryption_version' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'marriage_cert' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'marriage_verified' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'marriage_fail_reason' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'marriage_submitted_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'marriage_reviewed_at' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'face_method' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'face_vendor' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'face_score' AS column_name
    UNION ALL
    SELECT 'user_auth' AS table_name, 'id_card_issued' AS column_name
    UNION ALL
    SELECT 'user_privacy' AS table_name, 'anonymous_browse_enabled' AS column_name
    UNION ALL
    SELECT 'user_privacy' AS table_name, 'privacy_version' AS column_name
    UNION ALL
    SELECT 'user_privacy' AS table_name, 'privacy_updated_at' AS column_name
    UNION ALL
    SELECT 'user_report' AS table_name, 'submit_ip' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'login_status' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'failure_reason' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'session_id' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'device_id' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'platform' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'os_version' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'app_version' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'user_agent' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'region' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'risk_level' AS column_name
    UNION ALL
    SELECT 'user_login_log' AS table_name, 'is_suspicious' AS column_name
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE()
 AND c.TABLE_NAME = expected.table_name
 AND c.COLUMN_NAME = expected.column_name
WHERE c.COLUMN_NAME IS NULL;
