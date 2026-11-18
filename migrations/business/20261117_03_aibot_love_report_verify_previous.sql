SELECT 'aibot_love_report already exists' AS issue
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_love_report');
