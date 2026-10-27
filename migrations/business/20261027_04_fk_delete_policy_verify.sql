SELECT expected.table_name, expected.constraint_name,
       COALESCE(c.DELETE_RULE, 'MISSING') AS delete_rule
FROM (
    SELECT 'payment_order' AS table_name, 'fk_payment_order_user_id' AS constraint_name
    UNION ALL SELECT 'user_points' AS table_name, 'fk_user_points_user_id' AS constraint_name
    UNION ALL SELECT 'user_membership' AS table_name, 'fk_user_membership_user_id' AS constraint_name
    UNION ALL SELECT 'user_report' AS table_name, 'fk_user_report_user_id' AS constraint_name
    UNION ALL SELECT 'user_report' AS table_name, 'fk_user_report_target_user_id' AS constraint_name
    UNION ALL SELECT 'report_appeal' AS table_name, 'fk_report_appeal_appellant_user_id' AS constraint_name
) AS expected
LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS c
  ON c.CONSTRAINT_SCHEMA = DATABASE()
 AND c.TABLE_NAME = expected.table_name
 AND c.CONSTRAINT_NAME = expected.constraint_name
WHERE c.CONSTRAINT_NAME IS NULL OR c.DELETE_RULE <> 'RESTRICT';
