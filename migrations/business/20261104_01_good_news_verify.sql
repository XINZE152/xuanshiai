-- 20261104_01_good_news (verify)
-- 期望：五张关系表存在，关键列与索引齐全。

SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('good_news_meta', 'good_news_consent', 'good_news_blessing', 'good_news_report', 'good_news_audit')
  AND COLUMN_NAME IN (
      'news_id', 'stage', 'review_status', 'public_status', 'from_user_id', 'to_user_id',
      'matchmaker_id', 'display_mode', 'verification_level', 'blessing_count',
      'user_id', 'role', 'scopes_json', 'revoked_at', 'submitter_id', 'status',
      'idempotency_key', 'action', 'actor_user_id'
  )
ORDER BY TABLE_NAME, ORDINAL_POSITION;

SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('good_news_meta', 'good_news_consent', 'good_news_blessing', 'good_news_report', 'good_news_audit')
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX;
