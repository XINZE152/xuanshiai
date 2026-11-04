-- 20261104_01_good_news (verify_previous)
-- 期望：升级前五张关系表均不存在。

SELECT TABLE_NAME, COUNT(*) AS table_count
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('good_news_meta', 'good_news_consent', 'good_news_blessing', 'good_news_report', 'good_news_audit')
GROUP BY TABLE_NAME;
