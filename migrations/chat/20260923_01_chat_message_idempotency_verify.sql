-- Expected: nullable varchar(128) with case-sensitive utf8mb4_bin collation; the
-- unique index has the ordered columns, and duplicate scoped keys count is zero.
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLLATION_NAME
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'chat_message'
  AND COLUMN_NAME = 'client_message_id';

SELECT INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS indexed_columns
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'chat_message'
  AND INDEX_NAME = 'uq_chat_message_sender_session_client_message'
GROUP BY INDEX_NAME, NON_UNIQUE;

SELECT COUNT(*) AS duplicate_scoped_client_message_keys
FROM (
    SELECT from_user_id, session_id, client_message_id
    FROM chat_message
    WHERE client_message_id IS NOT NULL
    GROUP BY from_user_id, session_id, client_message_id
    HAVING COUNT(*) > 1
) AS duplicate_keys;
