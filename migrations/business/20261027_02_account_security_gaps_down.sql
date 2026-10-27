-- 20261027_02_account_security_gaps down：删除本迁移新增的列。
-- 警告：列中的存量数据会随列删除而丢失；仅在确认备份后执行。
SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @users_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME IN ('password_algo', 'password_updated_at', 'password_failed_count', 'password_locked_until', 'phone_verified_at', 'wechat_bound_at', 'last_login_ip', 'last_login_device_id', 'risk_status', 'frozen_at', 'frozen_reason', 'deletion_requested_at', 'deletion_scheduled_at', 'deletion_cancelled_at')
);
SET @users_drop = IF(@users_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `users` ', @users_drop_cols));
PREPARE users_drop_stmt FROM @users_drop;
EXECUTE users_drop_stmt;
DEALLOCATE PREPARE users_drop_stmt;

SET @user_auth_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_auth' AND COLUMN_NAME IN ('realname_provider', 'provider_request_id', 'provider_result_code', 'submitted_at', 'verified_at', 'failed_at', 'retry_count', 'next_retry_at', 'manual_review_by', 'manual_review_at', 'revoked_at', 'revoked_reason', 'id_card_masked', 'encryption_version', 'marriage_cert', 'marriage_verified', 'marriage_fail_reason', 'marriage_submitted_at', 'marriage_reviewed_at', 'face_method', 'face_vendor', 'face_score', 'id_card_issued')
);
SET @user_auth_drop = IF(@user_auth_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_auth` ', @user_auth_drop_cols));
PREPARE user_auth_drop_stmt FROM @user_auth_drop;
EXECUTE user_auth_drop_stmt;
DEALLOCATE PREPARE user_auth_drop_stmt;

SET @user_privacy_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_privacy' AND COLUMN_NAME IN ('anonymous_browse_enabled', 'privacy_version', 'privacy_updated_at')
);
SET @user_privacy_drop = IF(@user_privacy_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_privacy` ', @user_privacy_drop_cols));
PREPARE user_privacy_drop_stmt FROM @user_privacy_drop;
EXECUTE user_privacy_drop_stmt;
DEALLOCATE PREPARE user_privacy_drop_stmt;

SET @user_report_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_report' AND COLUMN_NAME IN ('submit_ip')
);
SET @user_report_drop = IF(@user_report_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_report` ', @user_report_drop_cols));
PREPARE user_report_drop_stmt FROM @user_report_drop;
EXECUTE user_report_drop_stmt;
DEALLOCATE PREPARE user_report_drop_stmt;

SET @user_login_log_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_login_log' AND COLUMN_NAME IN ('login_status', 'failure_reason', 'session_id', 'device_id', 'platform', 'os_version', 'app_version', 'user_agent', 'region', 'risk_level', 'is_suspicious')
);
SET @user_login_log_drop = IF(@user_login_log_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_login_log` ', @user_login_log_drop_cols));
PREPARE user_login_log_drop_stmt FROM @user_login_log_drop;
EXECUTE user_login_log_drop_stmt;
DEALLOCATE PREPARE user_login_log_drop_stmt;
