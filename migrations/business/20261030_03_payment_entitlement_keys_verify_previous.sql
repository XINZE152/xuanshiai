-- 20261030_03_payment_entitlement_keys 回滚后校验。
-- 返回 0 行表示 down 已恢复旧结构。

SELECT expected.table_name, expected.column_name, 'column not removed' AS issue
FROM (
    SELECT 'activity_signup' AS table_name, 'order_no' AS column_name
    UNION ALL SELECT 'activity_signup', 'pay_status'
    UNION ALL SELECT 'activity_signup', 'paid_amount'
) AS expected
JOIN INFORMATION_SCHEMA.COLUMNS actual
  ON actual.TABLE_SCHEMA = DATABASE()
 AND actual.TABLE_NAME = expected.table_name
 AND actual.COLUMN_NAME = expected.column_name

UNION ALL

SELECT 'user_membership', 'uk_user_membership_order_no', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_membership'
      AND INDEX_NAME = 'uk_user_membership_order_no'
)

UNION ALL

SELECT 'user_boost', 'uk_user_boost_order_no', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_boost'
      AND INDEX_NAME = 'uk_user_boost_order_no'
)

UNION ALL

SELECT 'activity_signup', 'uk_activity_signup_order', 'index not removed'
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'activity_signup'
      AND INDEX_NAME = 'uk_activity_signup_order'
);
