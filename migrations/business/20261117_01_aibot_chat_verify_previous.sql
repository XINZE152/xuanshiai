SELECT 'aibot_user_assignment already exists' AS issue
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_user_assignment')
UNION ALL
SELECT 'aibot_session already exists' AS issue
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_session')
UNION ALL
SELECT 'aibot_turn already exists' AS issue
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'aibot_turn');
