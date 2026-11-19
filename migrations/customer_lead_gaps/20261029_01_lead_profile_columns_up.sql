-- =============================================================================
-- 客源线索资料列迁移：real_name / photo / birthday（up）
--
-- 背景：2026-10-24 快照 035efa9 把「真实姓名、照片、出生日期」三列写进了
-- app/db/business_schema.py 的 customer_lead 建表语句，并被后台与工作台两侧
-- 无条件引用：
--   - app/services/customer_lead_admin.py LEAD_SELECT、insert_columns
--   - app/services/matchmaker_workspace.py _LEAD_SELECT（l.real_name/l.photo/l.birthday）
--     与 create_lead 的 INSERT 列
-- business_schema.py 只做 CREATE TABLE IF NOT EXISTS，不会给已存在的表补列，
-- 因此早于该快照建立的库（本地开发库即如此）执行上述查询会直接报
-- MySQL 1054 Unknown column 'l.real_name'，客源线索列表返回 500。
--
-- 影响：三列均 NULL 可空、无索引、无生成列依赖，纯追加；老数据取值为 NULL，
-- 前端按「未填」渲染。列位置追加在表尾即可，读写均按列名，不依赖顺序。
-- 可重复执行：AUTO_INIT_DB 已建好三列的新库会整条跳过。
-- =============================================================================

SET NAMES utf8mb4;

-- 幂等补列：real_name / photo / birthday（已存在的列不进入 ADD COLUMN 列表）
SET @cl_profile_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'real_name' AS column_name,
               '`real_name` varchar(64) DEFAULT NULL COMMENT ''客源真实姓名（选填）''' AS column_ddl
        UNION ALL
        SELECT 2 AS ord, 'photo' AS column_name,
               '`photo` varchar(512) DEFAULT NULL COMMENT ''客源照片URL''' AS column_ddl
        UNION ALL
        SELECT 3 AS ord, 'birthday' AS column_name,
               '`birthday` date DEFAULT NULL' AS column_ddl
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @cl_profile_ddl = IF(@cl_profile_missing IS NULL, 'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @cl_profile_missing));
PREPARE cl_profile_stmt FROM @cl_profile_ddl;
EXECUTE cl_profile_stmt;
DEALLOCATE PREPARE cl_profile_stmt;
