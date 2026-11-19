SELECT 'withdrawal_payee_secret missing' AS issue
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'withdrawal_payee_secret');
