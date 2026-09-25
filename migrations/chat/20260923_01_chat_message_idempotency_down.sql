-- Idempotently remove the idempotency index and key column.
-- This discards client_message_id values written after the up migration.
SET @chat_message_idempotency_index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_message'
      AND INDEX_NAME = 'uq_chat_message_sender_session_client_message'
);
SET @chat_message_idempotency_index_ddl = IF(
    @chat_message_idempotency_index_exists > 0,
    'ALTER TABLE `chat_message` DROP INDEX `uq_chat_message_sender_session_client_message`',
    'SELECT 1'
);
PREPARE chat_message_idempotency_index_stmt
    FROM @chat_message_idempotency_index_ddl;
EXECUTE chat_message_idempotency_index_stmt;
DEALLOCATE PREPARE chat_message_idempotency_index_stmt;

SET @chat_message_client_message_id_column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_message'
      AND COLUMN_NAME = 'client_message_id'
);
SET @chat_message_client_message_id_column_ddl = IF(
    @chat_message_client_message_id_column_exists > 0,
    'ALTER TABLE `chat_message` DROP COLUMN `client_message_id`',
    'SELECT 1'
);
PREPARE chat_message_client_message_id_column_stmt
    FROM @chat_message_client_message_id_column_ddl;
EXECUTE chat_message_client_message_id_column_stmt;
DEALLOCATE PREPARE chat_message_client_message_id_column_stmt;
