SELECT 'aibot_user_assignment missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_user_assignment')
UNION ALL
SELECT 'aibot_session missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_session')
UNION ALL
SELECT 'aibot_turn missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_turn');
