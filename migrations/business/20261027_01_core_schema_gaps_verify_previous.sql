-- 核心 schema 缺口迁移回滚后校验。
-- 返回 0 行表示 down 已恢复旧结构；任何返回行都表示回滚不完整。

SELECT expected.table_name,
       expected.column_name,
       'column not removed' AS issue
FROM (
    SELECT 'users' AS table_name, 'deleted_at' AS column_name
    UNION ALL SELECT 'user_auth', 'id_card_hash'
    UNION ALL SELECT 'user_auth', 'realname_status'
    UNION ALL SELECT 'chat_session', 'user1_pinned_at'
    UNION ALL SELECT 'chat_session', 'user2_pinned_at'
) AS expected
JOIN INFORMATION_SCHEMA.COLUMNS actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
 AND actual.COLUMN_NAME = expected.column_name

UNION ALL

SELECT 'users', 'idx_users_status_deleted', 'index not removed'
WHERE EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_status_deleted'
)

UNION ALL

SELECT 'user_auth', 'idx_user_auth_id_card_realname', 'index not removed'
WHERE EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND INDEX_NAME = 'idx_user_auth_id_card_realname'
);
