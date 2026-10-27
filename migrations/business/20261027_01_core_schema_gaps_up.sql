-- 核心账号生命周期与聊天置顶字段迁移（up）。
--
-- 背景：`users.deleted_at`、`user_auth.realname_status/id_card_hash`、
-- `chat_session.user1/2_pinned_at` 此前只由 database_setup_marriage.py 的
-- bootstrap 补列，AUTO_INIT_DB=false 的环境没有对应迁移。
--
-- 幂等约定：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE 动态 DDL；表或列已存在时跳过。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ 1. users.deleted_at ============
SET @users_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'deleted_at' AS column_name,
               '`deleted_at` datetime DEFAULT NULL COMMENT ''软删除时间；非空表示已注销或已删除''' AS column_ddl
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
PREPARE users_cols_stmt FROM @users_ddl;
EXECUTE users_cols_stmt;
DEALLOCATE PREPARE users_cols_stmt;

SET @users_index_exists = (
    SELECT COUNT(DISTINCT INDEX_NAME)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_status_deleted'
);
SET @users_columns_ready = (
    SELECT COUNT(DISTINCT COLUMN_NAME)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME IN ('status', 'deleted_at')
);
SET @users_index_ddl = IF(
    @users_index_exists > 0 OR @users_columns_ready < 2,
    'SELECT 1',
    'ALTER TABLE `users` ADD KEY `idx_users_status_deleted` (`status`, `deleted_at`)'
);
PREPARE users_index_stmt FROM @users_index_ddl;
EXECUTE users_index_stmt;
DEALLOCATE PREPARE users_index_stmt;

-- ============ 2. user_auth 实名认证与身份证去重 ============
SET @user_auth_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'id_card_hash' AS column_name,
               '`id_card_hash` char(64) DEFAULT NULL COMMENT ''身份证号哈希，用于去重''' AS column_ddl
        UNION ALL SELECT 2, 'realname_status',
               '`realname_status` tinyint NOT NULL DEFAULT ''0'' COMMENT ''0未认证 1认证中 2通过 3失败 4人工复核 5撤销'''
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
PREPARE user_auth_cols_stmt FROM @user_auth_ddl;
EXECUTE user_auth_cols_stmt;
DEALLOCATE PREPARE user_auth_cols_stmt;

SET @user_auth_index_exists = (
    SELECT COUNT(DISTINCT INDEX_NAME)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND INDEX_NAME = 'idx_user_auth_id_card_realname'
);
SET @user_auth_columns_ready = (
    SELECT COUNT(DISTINCT COLUMN_NAME)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND COLUMN_NAME IN ('id_card_hash', 'realname_status')
);
SET @user_auth_index_ddl = IF(
    @user_auth_index_exists > 0 OR @user_auth_columns_ready < 2,
    'SELECT 1',
    'ALTER TABLE `user_auth` ADD KEY `idx_user_auth_id_card_realname` (`id_card_hash`, `realname_status`)'
);
PREPARE user_auth_index_stmt FROM @user_auth_index_ddl;
EXECUTE user_auth_index_stmt;
DEALLOCATE PREPARE user_auth_index_stmt;

-- ============ 3. chat_session 双方置顶时间 ============
SET @chat_session_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'user1_pinned_at' AS column_name,
               '`user1_pinned_at` datetime DEFAULT NULL COMMENT ''user1 置顶会话时间''' AS column_ddl
        UNION ALL SELECT 2, 'user2_pinned_at',
               '`user2_pinned_at` datetime DEFAULT NULL COMMENT ''user2 置顶会话时间'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'chat_session'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'chat_session' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @chat_session_ddl = IF(
    @chat_session_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `chat_session` ', @chat_session_missing)
);
PREPARE chat_session_cols_stmt FROM @chat_session_ddl;
EXECUTE chat_session_cols_stmt;
DEALLOCATE PREPARE chat_session_cols_stmt;
