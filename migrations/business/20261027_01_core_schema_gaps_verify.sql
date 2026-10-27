-- 核心 schema 缺口迁移当前态校验。
-- 返回 0 行表示迁移完成；任何返回行都会阻断发布。

SELECT expected.table_name,
       expected.column_name,
       'missing column' AS issue
FROM (
    SELECT 'users' AS table_name, 'deleted_at' AS column_name
    UNION ALL SELECT 'user_auth', 'id_card_hash'
    UNION ALL SELECT 'user_auth', 'realname_status'
    UNION ALL SELECT 'chat_session', 'user1_pinned_at'
    UNION ALL SELECT 'chat_session', 'user2_pinned_at'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
 AND actual.COLUMN_NAME = expected.column_name
WHERE actual.COLUMN_NAME IS NULL

UNION ALL

SELECT 'users', 'idx_users_status_deleted', 'missing index'
WHERE NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'users'
      AND INDEX_NAME = 'idx_users_status_deleted'
)

UNION ALL

SELECT 'user_auth', 'idx_user_auth_id_card_realname', 'missing index'
WHERE NOT EXISTS (
    SELECT 1
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'user_auth'
      AND INDEX_NAME = 'idx_user_auth_id_card_realname'
);
