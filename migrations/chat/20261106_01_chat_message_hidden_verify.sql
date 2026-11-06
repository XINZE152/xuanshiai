-- Expected: chat_message_hidden exists with the message/user unique key.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'chat_message_hidden'
ORDER BY ORDINAL_POSITION;

SELECT INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS indexed_columns
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'chat_message_hidden'
GROUP BY INDEX_NAME, NON_UNIQUE;

SELECT COUNT(*) AS duplicate_hidden_keys
FROM (
    SELECT message_id, user_id FROM `chat_message_hidden`
    GROUP BY message_id, user_id HAVING COUNT(*) > 1
) AS duplicates;
