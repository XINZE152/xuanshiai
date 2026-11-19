-- 客源线索资料列迁移校验（verify）。
-- 执行 up.sql 后运行：任何一条结果与注释期望不符，即迁移未完成或被跳过。
-- Run with the intended database selected (MySQL 8+).

-- 1) 三列齐备性：期望返回 0 行；任何返回行都是缺失的列。
SELECT w.column_name AS missing_column
FROM (
    SELECT 'real_name' AS column_name, 'varchar' AS expected_type, 'YES' AS expected_nullable
    UNION ALL SELECT 'photo', 'varchar', 'YES'
    UNION ALL SELECT 'birthday', 'date', 'YES'
) AS w
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
WHERE c.COLUMN_NAME IS NULL
   OR c.DATA_TYPE <> w.expected_type
   OR c.IS_NULLABLE <> w.expected_nullable;

-- 2) 真实姓名/照片长度与建表语句一致：期望 real_name=64、photo=512、birthday=NULL。
SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH, DATETIME_PRECISION, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead'
  AND COLUMN_NAME IN ('real_name', 'photo', 'birthday')
ORDER BY COLUMN_NAME;

-- 3) 无新增索引/生成列依赖：期望返回 0 行。
SELECT INDEX_NAME, COLUMN_NAME
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead'
  AND COLUMN_NAME IN ('real_name', 'photo', 'birthday');
