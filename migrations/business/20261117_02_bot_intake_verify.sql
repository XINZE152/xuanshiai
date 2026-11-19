SELECT 'user_profile.bot_intake_json missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'user_profile' AND column_name = 'bot_intake_json');
