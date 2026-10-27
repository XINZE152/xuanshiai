-- 20261027_02_account_security_gaps
-- 由 _ensure_required_columns 的补列定义生成：只补基础 CREATE TABLE 中不存在的列。
-- 幂等约定：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE；列已存在时跳过。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ users（14 列） ============
SET @users_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'password_algo' AS column_name, '`password_algo` varchar(32) DEFAULT NULL COMMENT ''密码哈希算法''' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'password_updated_at' AS column_name, '`password_updated_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'password_failed_count' AS column_name, '`password_failed_count` int unsigned NOT NULL DEFAULT ''0''' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'password_locked_until' AS column_name, '`password_locked_until` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 5 AS ord, 'phone_verified_at' AS column_name, '`phone_verified_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 6 AS ord, 'wechat_bound_at' AS column_name, '`wechat_bound_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 7 AS ord, 'last_login_ip' AS column_name, '`last_login_ip` varchar(64) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 8 AS ord, 'last_login_device_id' AS column_name, '`last_login_device_id` varchar(128) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 9 AS ord, 'risk_status' AS column_name, '`risk_status` tinyint NOT NULL DEFAULT ''0'' COMMENT ''0正常 1关注 2限制''' AS column_ddl
        UNION ALL SELECT 10 AS ord, 'frozen_at' AS column_name, '`frozen_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 11 AS ord, 'frozen_reason' AS column_name, '`frozen_reason` varchar(255) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 12 AS ord, 'deletion_requested_at' AS column_name, '`deletion_requested_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 13 AS ord, 'deletion_scheduled_at' AS column_name, '`deletion_scheduled_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 14 AS ord, 'deletion_cancelled_at' AS column_name, '`deletion_cancelled_at` datetime DEFAULT NULL' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'users'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'users' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @users_ddl = IF(
    @users_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `users` ', @users_missing)
);
PREPARE users_stmt FROM @users_ddl;
EXECUTE users_stmt;
DEALLOCATE PREPARE users_stmt;

-- ============ user_auth（23 列） ============
SET @user_auth_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'realname_provider' AS column_name, '`realname_provider` varchar(64) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'provider_request_id' AS column_name, '`provider_request_id` varchar(128) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'provider_result_code' AS column_name, '`provider_result_code` varchar(64) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'submitted_at' AS column_name, '`submitted_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 5 AS ord, 'verified_at' AS column_name, '`verified_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 6 AS ord, 'failed_at' AS column_name, '`failed_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 7 AS ord, 'retry_count' AS column_name, '`retry_count` int unsigned NOT NULL DEFAULT ''0''' AS column_ddl
        UNION ALL SELECT 8 AS ord, 'next_retry_at' AS column_name, '`next_retry_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 9 AS ord, 'manual_review_by' AS column_name, '`manual_review_by` bigint unsigned DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 10 AS ord, 'manual_review_at' AS column_name, '`manual_review_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 11 AS ord, 'revoked_at' AS column_name, '`revoked_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 12 AS ord, 'revoked_reason' AS column_name, '`revoked_reason` varchar(255) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 13 AS ord, 'id_card_masked' AS column_name, '`id_card_masked` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 14 AS ord, 'encryption_version' AS column_name, '`encryption_version` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 15 AS ord, 'marriage_cert' AS column_name, '`marriage_cert` varchar(255) DEFAULT NULL COMMENT ''婚姻认证材料描述或地址''' AS column_ddl
        UNION ALL SELECT 16 AS ord, 'marriage_verified' AS column_name, '`marriage_verified` tinyint NOT NULL DEFAULT ''0'' COMMENT ''0未提交 1审核中 2通过 3失败''' AS column_ddl
        UNION ALL SELECT 17 AS ord, 'marriage_fail_reason' AS column_name, '`marriage_fail_reason` varchar(255) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 18 AS ord, 'marriage_submitted_at' AS column_name, '`marriage_submitted_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 19 AS ord, 'marriage_reviewed_at' AS column_name, '`marriage_reviewed_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 20 AS ord, 'face_method' AS column_name, '`face_method` varchar(32) DEFAULT NULL COMMENT ''验证方式 动作活检/照片比对''' AS column_ddl
        UNION ALL SELECT 21 AS ord, 'face_vendor' AS column_name, '`face_vendor` varchar(64) DEFAULT NULL COMMENT ''人脸服务商''' AS column_ddl
        UNION ALL SELECT 22 AS ord, 'face_score' AS column_name, '`face_score` decimal(5,2) DEFAULT NULL COMMENT ''人脸比对得分''' AS column_ddl
        UNION ALL SELECT 23 AS ord, 'id_card_issued' AS column_name, '`id_card_issued` varchar(64) DEFAULT NULL COMMENT ''身份证发证机关''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_auth'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_auth' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_auth_ddl = IF(
    @user_auth_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_auth` ', @user_auth_missing)
);
PREPARE user_auth_stmt FROM @user_auth_ddl;
EXECUTE user_auth_stmt;
DEALLOCATE PREPARE user_auth_stmt;

-- ============ user_privacy（3 列） ============
SET @user_privacy_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'anonymous_browse_enabled' AS column_name, '`anonymous_browse_enabled` tinyint NOT NULL DEFAULT ''0'' COMMENT ''VIP无痕浏览''' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'privacy_version' AS column_name, '`privacy_version` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'privacy_updated_at' AS column_name, '`privacy_updated_at` datetime DEFAULT NULL' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_privacy'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_privacy' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_privacy_ddl = IF(
    @user_privacy_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_privacy` ', @user_privacy_missing)
);
PREPARE user_privacy_stmt FROM @user_privacy_ddl;
EXECUTE user_privacy_stmt;
DEALLOCATE PREPARE user_privacy_stmt;

-- ============ user_report（1 列） ============
SET @user_report_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'submit_ip' AS column_name, '`submit_ip` varchar(64) DEFAULT NULL COMMENT ''提交人IP''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_report'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_report' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_report_ddl = IF(
    @user_report_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_report` ', @user_report_missing)
);
PREPARE user_report_stmt FROM @user_report_ddl;
EXECUTE user_report_stmt;
DEALLOCATE PREPARE user_report_stmt;

-- ============ user_login_log（11 列） ============
SET @user_login_log_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'login_status' AS column_name, '`login_status` tinyint NOT NULL DEFAULT ''1'' COMMENT ''1成功 2失败''' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'failure_reason' AS column_name, '`failure_reason` varchar(255) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'session_id' AS column_name, '`session_id` bigint unsigned DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'device_id' AS column_name, '`device_id` varchar(128) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 5 AS ord, 'platform' AS column_name, '`platform` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 6 AS ord, 'os_version' AS column_name, '`os_version` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 7 AS ord, 'app_version' AS column_name, '`app_version` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 8 AS ord, 'user_agent' AS column_name, '`user_agent` varchar(512) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 9 AS ord, 'region' AS column_name, '`region` varchar(128) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 10 AS ord, 'risk_level' AS column_name, '`risk_level` tinyint NOT NULL DEFAULT ''0''' AS column_ddl
        UNION ALL SELECT 11 AS ord, 'is_suspicious' AS column_name, '`is_suspicious` tinyint NOT NULL DEFAULT ''0''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_login_log'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_login_log' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_login_log_ddl = IF(
    @user_login_log_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_login_log` ', @user_login_log_missing)
);
PREPARE user_login_log_stmt FROM @user_login_log_ddl;
EXECUTE user_login_log_stmt;
DEALLOCATE PREPARE user_login_log_stmt;
