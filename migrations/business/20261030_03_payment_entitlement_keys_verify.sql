-- 20261030_03_payment_entitlement_keys 当前态校验。
-- 返回 0 行表示迁移完成。

SELECT expected.table_name, expected.column_name, 'missing column' AS issue
FROM (
    SELECT 'activity_signup' AS table_name, 'order_no' AS column_name
    UNION ALL SELECT 'activity_signup', 'pay_status'
    UNION ALL SELECT 'activity_signup', 'paid_amount'
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
 AND actual.COLUMN_NAME = expected.column_name
WHERE actual.COLUMN_NAME IS NULL

UNION ALL

SELECT 'user_membership', 'uk_user_membership_order_no', 'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership'
      AND INDEX_NAME = 'uk_user_membership_order_no'
)

UNION ALL

SELECT 'user_boost', 'uk_user_boost_order_no', 'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_boost'
      AND INDEX_NAME = 'uk_user_boost_order_no'
)

UNION ALL

SELECT 'activity_signup', 'uk_activity_signup_order', 'missing index'
WHERE NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'uk_activity_signup_order'
);
