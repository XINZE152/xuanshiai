-- 20261104_01_good_news (verify)
-- 返回 0 行表示迁移完成（runner 的 _verify 只接受单条查询）。

SELECT expected.table_name, expected.column_name, 'missing column' AS issue
FROM (
    SELECT 'good_news_meta' AS table_name, 'news_id' AS column_name
    UNION ALL SELECT 'good_news_meta', 'stage'
    UNION ALL SELECT 'good_news_meta', 'review_status'
    UNION ALL SELECT 'good_news_meta', 'public_status'
    UNION ALL SELECT 'good_news_meta', 'from_user_id'
    UNION ALL SELECT 'good_news_meta', 'to_user_id'
    UNION ALL SELECT 'good_news_meta', 'matchmaker_id'
    UNION ALL SELECT 'good_news_meta', 'display_mode'
    UNION ALL SELECT 'good_news_meta', 'verification_level'
    UNION ALL SELECT 'good_news_meta', 'blessing_count'
    UNION ALL SELECT 'good_news_consent', 'news_id'
    UNION ALL SELECT 'good_news_consent', 'user_id'
    UNION ALL SELECT 'good_news_consent', 'role'
    UNION ALL SELECT 'good_news_consent', 'scopes_json'
    UNION ALL SELECT 'good_news_consent', 'revoked_at'
    UNION ALL SELECT 'good_news_blessing', 'news_id'
    UNION ALL SELECT 'good_news_blessing', 'user_id'
    UNION ALL SELECT 'good_news_report', 'news_id'
    UNION ALL SELECT 'good_news_report', 'submitter_id'
    UNION ALL SELECT 'good_news_report', 'status'
    UNION ALL SELECT 'good_news_report', 'idempotency_key'
    UNION ALL SELECT 'good_news_audit', 'news_id'
    UNION ALL SELECT 'good_news_audit', 'actor_user_id'
    UNION ALL SELECT 'good_news_audit', 'action'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = expected.table_name
   AND actual.COLUMN_NAME = expected.column_name
WHERE actual.column_name IS NULL

UNION ALL

SELECT expected.table_name, expected.index_name, 'missing index' AS issue
FROM (
    SELECT 'good_news_meta' AS table_name, 'idx_good_news_meta_public' AS index_name
    UNION ALL SELECT 'good_news_consent', 'uk_good_news_consent_role'
    UNION ALL SELECT 'good_news_blessing', 'uk_good_news_blessing'
    UNION ALL SELECT 'good_news_report', 'uk_good_news_report_idem'
    UNION ALL SELECT 'good_news_audit', 'idx_good_news_audit_news'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.STATISTICS actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = expected.table_name
   AND actual.INDEX_NAME = expected.index_name
WHERE actual.index_name IS NULL

UNION ALL

SELECT expected.table_name, NULL, 'missing table' AS issue
FROM (
    SELECT 'good_news_meta' AS table_name
    UNION ALL SELECT 'good_news_consent'
    UNION ALL SELECT 'good_news_blessing'
    UNION ALL SELECT 'good_news_report'
    UNION ALL SELECT 'good_news_audit'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.TABLES actual
    ON actual.TABLE_SCHEMA = DATABASE()
   AND actual.TABLE_NAME = expected.table_name
WHERE actual.table_name IS NULL;
