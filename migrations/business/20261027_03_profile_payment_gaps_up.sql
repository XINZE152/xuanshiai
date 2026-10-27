-- 20261027_03_profile_payment_gaps
-- 由 _ensure_required_columns 的补列定义生成：只补基础 CREATE TABLE 中不存在的列。
-- 幂等约定：INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE；列已存在时跳过。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

-- ============ user_profile（35 列） ============
SET @user_profile_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'weight' AS column_name, '`weight` int DEFAULT NULL COMMENT ''体重kg''' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'zodiac' AS column_name, '`zodiac` varchar(16) DEFAULT NULL COMMENT ''属相''' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'household' AS column_name, '`household` varchar(128) DEFAULT NULL COMMENT ''户籍''' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'ethnicity' AS column_name, '`ethnicity` varchar(32) DEFAULT NULL COMMENT ''民族''' AS column_ddl
        UNION ALL SELECT 5 AS ord, 'house' AS column_name, '`house` varchar(32) DEFAULT NULL COMMENT ''购房情况''' AS column_ddl
        UNION ALL SELECT 6 AS ord, 'car' AS column_name, '`car` varchar(32) DEFAULT NULL COMMENT ''购车情况''' AS column_ddl
        UNION ALL SELECT 7 AS ord, 'smoking' AS column_name, '`smoking` varchar(32) DEFAULT NULL COMMENT ''吸烟情况''' AS column_ddl
        UNION ALL SELECT 8 AS ord, 'drinking' AS column_name, '`drinking` varchar(32) DEFAULT NULL COMMENT ''饮酒情况''' AS column_ddl
        UNION ALL SELECT 9 AS ord, 'religion' AS column_name, '`religion` varchar(32) DEFAULT NULL COMMENT ''宗教信仰''' AS column_ddl
        UNION ALL SELECT 10 AS ord, 'marriage_plan' AS column_name, '`marriage_plan` varchar(64) DEFAULT NULL COMMENT ''结婚计划''' AS column_ddl
        UNION ALL SELECT 11 AS ord, 'household_province_code' AS column_name, '`household_province_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 12 AS ord, 'household_city_code' AS column_name, '`household_city_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 13 AS ord, 'household_district_code' AS column_name, '`household_district_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 14 AS ord, 'occupation' AS column_name, '`occupation` varchar(128) DEFAULT NULL COMMENT ''职业''' AS column_ddl
        UNION ALL SELECT 15 AS ord, 'industry' AS column_name, '`industry` varchar(128) DEFAULT NULL COMMENT ''行业''' AS column_ddl
        UNION ALL SELECT 16 AS ord, 'education_level' AS column_name, '`education_level` tinyint DEFAULT NULL COMMENT ''学历等级''' AS column_ddl
        UNION ALL SELECT 17 AS ord, 'hometown_province_code' AS column_name, '`hometown_province_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 18 AS ord, 'hometown_city_code' AS column_name, '`hometown_city_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 19 AS ord, 'hometown_district_code' AS column_name, '`hometown_district_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 20 AS ord, 'residence_province_code' AS column_name, '`residence_province_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 21 AS ord, 'residence_city_code' AS column_name, '`residence_city_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 22 AS ord, 'residence_district_code' AS column_name, '`residence_district_code` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 23 AS ord, 'community_city_name' AS column_name, '`community_city_name` varchar(64) DEFAULT NULL COMMENT ''同城浏览城市名''' AS column_ddl
        UNION ALL SELECT 24 AS ord, 'community_city_code' AS column_name, '`community_city_code` varchar(32) DEFAULT NULL COMMENT ''同城浏览市一级码''' AS column_ddl
        UNION ALL SELECT 25 AS ord, 'community_city_updated_at' AS column_name, '`community_city_updated_at` datetime DEFAULT NULL COMMENT ''同城偏好上次变更时间''' AS column_ddl
        UNION ALL SELECT 26 AS ord, 'location_source' AS column_name, '`location_source` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 27 AS ord, 'location_updated_at' AS column_name, '`location_updated_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 28 AS ord, 'location_precision' AS column_name, '`location_precision` decimal(10,2) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 29 AS ord, 'location_consent' AS column_name, '`location_consent` tinyint NOT NULL DEFAULT ''0''' AS column_ddl
        UNION ALL SELECT 30 AS ord, 'location_visible' AS column_name, '`location_visible` tinyint NOT NULL DEFAULT ''0''' AS column_ddl
        UNION ALL SELECT 31 AS ord, 'interest_tags' AS column_name, '`interest_tags` json DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 32 AS ord, 'personality_tags' AS column_name, '`personality_tags` json DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 33 AS ord, 'completion_algorithm_version' AS column_name, '`completion_algorithm_version` varchar(32) DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 34 AS ord, 'completion_calculated_at' AS column_name, '`completion_calculated_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 35 AS ord, 'matchmaker_note' AS column_name, '`matchmaker_note` varchar(2000) DEFAULT NULL COMMENT ''红娘说（后台红娘对会员的备注）''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_profile'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_profile' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_profile_ddl = IF(
    @user_profile_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_profile` ', @user_profile_missing)
);
PREPARE user_profile_stmt FROM @user_profile_ddl;
EXECUTE user_profile_stmt;
DEALLOCATE PREPARE user_profile_stmt;

-- ============ payment_order（4 列） ============
SET @payment_order_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'service_product_id' AS column_name, '`service_product_id` bigint unsigned DEFAULT NULL COMMENT ''红娘服务商品ID''' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'matchmaker_id' AS column_name, '`matchmaker_id` bigint unsigned DEFAULT NULL COMMENT ''红娘服务对象ID''' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'service_request_id' AS column_name, '`service_request_id` bigint unsigned DEFAULT NULL COMMENT ''关联红娘服务申请ID''' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'service_requirement' AS column_name, '`service_requirement` varchar(2000) DEFAULT NULL COMMENT ''红娘服务需求''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'payment_order'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'payment_order' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @payment_order_ddl = IF(
    @payment_order_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `payment_order` ', @payment_order_missing)
);
PREPARE payment_order_stmt FROM @payment_order_ddl;
EXECUTE payment_order_stmt;
DEALLOCATE PREPARE payment_order_stmt;

-- ============ user_boost（5 列） ============
SET @user_boost_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'start_at' AS column_name, '`start_at` datetime DEFAULT CURRENT_TIMESTAMP' AS column_ddl
        UNION ALL SELECT 2 AS ord, 'end_at' AS column_name, '`end_at` datetime DEFAULT NULL' AS column_ddl
        UNION ALL SELECT 3 AS ord, 'status' AS column_name, '`status` tinyint NOT NULL DEFAULT ''1'' COMMENT ''1生效中 2已过期 3已撤销''' AS column_ddl
        UNION ALL SELECT 4 AS ord, 'pay_status' AS column_name, '`pay_status` tinyint NOT NULL DEFAULT ''0'' COMMENT ''0未支付 1已支付''' AS column_ddl
        UNION ALL SELECT 5 AS ord, 'pay_method' AS column_name, '`pay_method` varchar(32) DEFAULT NULL COMMENT ''支付方式''' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'user_boost'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'user_boost' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @user_boost_ddl = IF(
    @user_boost_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `user_boost` ', @user_boost_missing)
);
PREPARE user_boost_stmt FROM @user_boost_ddl;
EXECUTE user_boost_stmt;
DEALLOCATE PREPARE user_boost_stmt;
