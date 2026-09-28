-- Idempotently add the nullable client key and scoped unique constraint.
-- Run with the intended database selected (MySQL 8+).
SET @chat_message_client_message_id_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_message'
      AND COLUMN_NAME = 'client_message_id'
);
SET @chat_message_client_message_id_collation = (
    SELECT COLLATION_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_message'
      AND COLUMN_NAME = 'client_message_id'
    LIMIT 1
);
SET @chat_message_client_message_id_column_ddl = IF(
    @chat_message_client_message_id_column_exists = 0,
    'ALTER TABLE `chat_message` ADD COLUMN `client_message_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT ''客户端消息幂等键'' AFTER `media_url`',
    IF(
        COALESCE(@chat_message_client_message_id_collation, '') <> 'utf8mb4_bin',
        'ALTER TABLE `chat_message` MODIFY COLUMN `client_message_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL COMMENT ''客户端消息幂等键'' AFTER `media_url`',
        'SELECT 1'
    )
);
PREPARE chat_message_client_message_id_column_stmt
    FROM @chat_message_client_message_id_column_ddl;
EXECUTE chat_message_client_message_id_column_stmt;
DEALLOCATE PREPARE chat_message_client_message_id_column_stmt;

SET @chat_message_idempotency_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_message'
      AND INDEX_NAME = 'uq_chat_message_sender_session_client_message'
);
SET @chat_message_idempotency_index_ddl = IF(
    @chat_message_idempotency_index_exists = 0,
    'ALTER TABLE `chat_message` ADD UNIQUE KEY `uq_chat_message_sender_session_client_message` (`from_user_id`, `session_id`, `client_message_id`)',
    'SELECT 1'
);
PREPARE chat_message_idempotency_index_stmt
    FROM @chat_message_idempotency_index_ddl;
EXECUTE chat_message_idempotency_index_stmt;
DEALLOCATE PREPARE chat_message_idempotency_index_stmt;
