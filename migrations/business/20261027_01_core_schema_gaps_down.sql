-- 核心账号生命周期与聊天置顶字段迁移（down）。
--
-- 本迁移会删除新增列及其数据。执行前必须备份 users、user_auth、chat_session；
-- 已产生业务数据的环境默认禁止 down。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ 1. users：索引和 deleted_at ============
SET @users_index_exists = (
    SELECT COUNT(DISTINCT INDEX_NAME)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_status_deleted'
);
SET @users_index_drop = IF(
    @users_index_exists = 0,
    'SELECT 1',
    'ALTER TABLE `users` DROP INDEX `idx_users_status_deleted`'
);
PREPARE users_index_drop_stmt FROM @users_index_drop;
EXECUTE users_index_drop_stmt;
DEALLOCATE PREPARE users_index_drop_stmt;

SET @users_columns = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND COLUMN_NAME = 'deleted_at'
);
SET @users_drop = IF(
    @users_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `users` ', @users_columns)
);
PREPARE users_drop_stmt FROM @users_drop;
EXECUTE users_drop_stmt;
DEALLOCATE PREPARE users_drop_stmt;

-- ============ 2. user_auth：索引与认证列 ============
SET @user_auth_index_exists = (
    SELECT COUNT(DISTINCT INDEX_NAME)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND INDEX_NAME = 'idx_user_auth_id_card_realname'
);
SET @user_auth_index_drop = IF(
    @user_auth_index_exists = 0,
    'SELECT 1',
    'ALTER TABLE `user_auth` DROP INDEX `idx_user_auth_id_card_realname`'
);
PREPARE user_auth_index_drop_stmt FROM @user_auth_index_drop;
EXECUTE user_auth_index_drop_stmt;
DEALLOCATE PREPARE user_auth_index_drop_stmt;

SET @user_auth_columns = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND COLUMN_NAME IN ('realname_status', 'id_card_hash')
);
SET @user_auth_drop = IF(
    @user_auth_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_auth` ', @user_auth_columns)
);
PREPARE user_auth_drop_stmt FROM @user_auth_drop;
EXECUTE user_auth_drop_stmt;
DEALLOCATE PREPARE user_auth_drop_stmt;

-- ============ 3. chat_session：置顶时间 ============
SET @chat_columns = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_session'
      AND COLUMN_NAME IN ('user1_pinned_at', 'user2_pinned_at')
);
SET @chat_drop = IF(
    @chat_columns IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `chat_session` ', @chat_columns)
);
PREPARE chat_drop_stmt FROM @chat_drop;
EXECUTE chat_drop_stmt;
DEALLOCATE PREPARE chat_drop_stmt;
