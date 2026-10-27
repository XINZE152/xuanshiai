-- 客源线索择偶要求暂存表迁移校验（verify）。
-- 在执行 up.sql 后运行：任何一条查询结果与注释中的期望不符，即迁移未完成或被跳过。
-- Run with the intended database selected (MySQL 8+).

-- 1) 表存在性：期望返回 1 行（TABLE_NAME='customer_lead_preference'）。
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead_preference';

-- 2) 关键列齐备性：期望返回 0 行；任何返回行都是缺失的列。
SELECT w.column_name AS missing_column
FROM (
    SELECT 'lead_id' AS column_name
    UNION ALL SELECT 'age_min'
    UNION ALL SELECT 'age_max'
    UNION ALL SELECT 'height_min'
    UNION ALL SELECT 'height_max'
    UNION ALL SELECT 'education_min'
    UNION ALL SELECT 'income_min'
    UNION ALL SELECT 'marriage_status'
    UNION ALL SELECT 'preferred_province_code'
    UNION ALL SELECT 'preferred_city_codes'
    UNION ALL SELECT 'accept_long_distance'
    UNION ALL SELECT 'accept_cross_province'
    UNION ALL SELECT 'housing_requirement'
    UNION ALL SELECT 'smoking_requirement'
    UNION ALL SELECT 'drinking_requirement'
    UNION ALL SELECT 'dating_goal'
    UNION ALL SELECT 'meeting_pace'
    UNION ALL SELECT 'children_intention'
    UNION ALL SELECT 'extra_requirement'
) AS w
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead_preference'
     AND c.COLUMN_NAME = w.column_name
WHERE c.COLUMN_NAME IS NULL;

-- 3) lead_id 唯一键：期望返回 1 行，INDEX_NAME='uk_lead_preference_lead'、NON_UNIQUE=0。
SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'customer_lead_preference'
  AND COLUMN_NAME = 'lead_id';
