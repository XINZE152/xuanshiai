SELECT 'aibot_love_report missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_love_report');
