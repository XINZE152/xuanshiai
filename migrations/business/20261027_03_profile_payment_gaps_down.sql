-- 20261027_03_profile_payment_gaps down：删除本迁移新增的列。
-- 警告：列中的存量数据会随列删除而丢失；仅在确认备份后执行。
SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @user_profile_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_profile' AND COLUMN_NAME IN ('weight', 'zodiac', 'household', 'ethnicity', 'house', 'car', 'smoking', 'drinking', 'religion', 'marriage_plan', 'household_province_code', 'household_city_code', 'household_district_code', 'occupation', 'industry', 'education_level', 'hometown_province_code', 'hometown_city_code', 'hometown_district_code', 'residence_province_code', 'residence_city_code', 'residence_district_code', 'community_city_name', 'community_city_code', 'community_city_updated_at', 'location_source', 'location_updated_at', 'location_precision', 'location_consent', 'location_visible', 'interest_tags', 'personality_tags', 'completion_algorithm_version', 'completion_calculated_at', 'matchmaker_note')
);
SET @user_profile_drop = IF(@user_profile_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_profile` ', @user_profile_drop_cols));
PREPARE user_profile_drop_stmt FROM @user_profile_drop;
EXECUTE user_profile_drop_stmt;
DEALLOCATE PREPARE user_profile_drop_stmt;

SET @payment_order_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payment_order' AND COLUMN_NAME IN ('service_product_id', 'matchmaker_id', 'service_request_id', 'service_requirement')
);
SET @payment_order_drop = IF(@payment_order_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `payment_order` ', @payment_order_drop_cols));
PREPARE payment_order_drop_stmt FROM @payment_order_drop;
EXECUTE payment_order_drop_stmt;
DEALLOCATE PREPARE payment_order_drop_stmt;

SET @user_boost_drop_cols = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', COLUMN_NAME, '`') ORDER BY ORDINAL_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'user_boost' AND COLUMN_NAME IN ('start_at', 'end_at', 'status', 'pay_status', 'pay_method')
);
SET @user_boost_drop = IF(@user_boost_drop_cols IS NULL, 'SELECT 1', CONCAT('ALTER TABLE `user_boost` ', @user_boost_drop_cols));
PREPARE user_boost_drop_stmt FROM @user_boost_drop;
EXECUTE user_boost_drop_stmt;
DEALLOCATE PREPARE user_boost_drop_stmt;
