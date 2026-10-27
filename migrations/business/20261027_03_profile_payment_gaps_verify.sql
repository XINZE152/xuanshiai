SELECT expected.table_name, expected.column_name
FROM (
    SELECT 'user_profile' AS table_name, 'weight' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'zodiac' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'household' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'ethnicity' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'house' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'car' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'smoking' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'drinking' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'religion' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'marriage_plan' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'household_province_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'household_city_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'household_district_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'occupation' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'industry' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'education_level' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'hometown_province_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'hometown_city_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'hometown_district_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'residence_province_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'residence_city_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'residence_district_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'community_city_name' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'community_city_code' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'community_city_updated_at' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'location_source' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'location_updated_at' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'location_precision' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'location_consent' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'location_visible' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'interest_tags' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'personality_tags' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'completion_algorithm_version' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'completion_calculated_at' AS column_name
    UNION ALL
    SELECT 'user_profile' AS table_name, 'matchmaker_note' AS column_name
    UNION ALL
    SELECT 'payment_order' AS table_name, 'service_product_id' AS column_name
    UNION ALL
    SELECT 'payment_order' AS table_name, 'matchmaker_id' AS column_name
    UNION ALL
    SELECT 'payment_order' AS table_name, 'service_request_id' AS column_name
    UNION ALL
    SELECT 'payment_order' AS table_name, 'service_requirement' AS column_name
    UNION ALL
    SELECT 'user_boost' AS table_name, 'start_at' AS column_name
    UNION ALL
    SELECT 'user_boost' AS table_name, 'end_at' AS column_name
    UNION ALL
    SELECT 'user_boost' AS table_name, 'status' AS column_name
    UNION ALL
    SELECT 'user_boost' AS table_name, 'pay_status' AS column_name
    UNION ALL
    SELECT 'user_boost' AS table_name, 'pay_method' AS column_name
) AS expected
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE()
 AND c.TABLE_NAME = expected.table_name
 AND c.COLUMN_NAME = expected.column_name
WHERE c.COLUMN_NAME IS NULL;
