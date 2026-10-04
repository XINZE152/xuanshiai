-- =============================================================================
-- 活动报名软删除列迁移（verify）
--
-- 在 up 之后执行；期望所有查询返回 0 行（最后一条除外，用于确认索引列序）。
-- =============================================================================

SET NAMES utf8mb4;

-- 1. 两列齐备性：期望 0 行
SELECT '期望 0 行：缺失的软删除列' AS check_item,
       t.TABLE_NAME,
       w.column_name AS missing_column
FROM (
    SELECT 'deleted_at' AS column_name
    UNION ALL SELECT 'deleted_by'
) AS w
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'activity_signup'
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
  ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'activity_signup' AND c.COLUMN_NAME = w.column_name
WHERE c.COLUMN_NAME IS NULL;

-- 2. 列属性：期望 deleted_at 可空 datetime、deleted_by 可空 bigint
SELECT '期望两行且 IS_NULLABLE=YES' AS check_item,
       COLUMN_NAME,
       COLUMN_TYPE,
       IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'activity_signup'
  AND COLUMN_NAME IN ('deleted_at', 'deleted_by')
ORDER BY COLUMN_NAME;

-- 3. 索引：期望 NON_UNIQUE=1、COLUMN_NAME=deleted_at
SELECT '期望一行' AS check_item,
       INDEX_NAME,
       NON_UNIQUE,
       SEQ_IN_INDEX,
       COLUMN_NAME
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'activity_signup'
  AND INDEX_NAME = 'idx_activity_signup_deleted';

-- 4. 存量数据：期望 0 行（历史物理删除无法追溯，此处仅确认无异常标记）
SELECT '期望 0 行：非法软删除标记' AS check_item, COUNT(*) AS bad_rows
FROM activity_signup
WHERE (deleted_at IS NULL AND deleted_by IS NOT NULL)
   OR (deleted_at IS NOT NULL AND deleted_by IS NULL);
