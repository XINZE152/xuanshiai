-- Irreversibly removes encrypted payee data. Back up the table before rollback.
DROP TABLE IF EXISTS withdrawal_payee_secret;
