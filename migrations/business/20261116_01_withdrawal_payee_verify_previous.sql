SELECT 'withdrawal_payee_secret already exists' AS issue
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'withdrawal_payee_secret');
