-- 后台积分发放/扣减补列迁移（A12-积分修复）校验（verify）。
-- 在执行 up.sql 后运行：任何一条查询结果与注释中的期望不符，即迁移未完成或被跳过。
-- Run with the intended database selected (MySQL 8+).

-- 1) 补列齐备性：期望返回 0 行；任何返回行都是缺失的列。
SELECT w.table_name AS missing_table, w.column_name AS missing_column
FROM (
    SELECT 'user_points' AS table_name, 'source_type' AS column_name
    UNION ALL
    SELECT 'user_points' AS table_name, 'operator_id' AS column_name
) AS w
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = w.table_name
     AND c.COLUMN_NAME = w.column_name
WHERE c.COLUMN_NAME IS NULL;

-- 2) 列定义明细：期望返回 2 行，均可空（IS_NULLABLE='YES'）、默认 NULL，与 up.sql 一致。
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'user_points'
  AND COLUMN_NAME IN ('source_type', 'operator_id');
