-- =============================================================================
-- 客源线索「本人资料」补列迁移（2026-10-28）（up）
--
-- 背景：对标站「新建客源 / 客源详情」表单带一大堆本人资料（真实姓名、出生、
-- 体重、年收入、购房、购车、吸烟、饮酒、民族、学校、单位、相册）。其中年龄/
-- 身高/婚况/学历/职业/现居/家乡 7 列已由 migrations/filters/20261011_01 补入
-- （当时仅供列表筛选）；本迁移补齐其余 12 列，使其能被建单/详情/更新读写。
--
-- 口径：与账号本人资料（user_profile / user_auth / users）对齐——枚举类一律
-- varchar 存原值（档位字典在 platform_base_data），身高/体重 int、年收入 varchar
-- 档位、相册 JSON。gender 列已存在，不在此补。birth_date 存日期，age 由服务层派生。
--
-- 幂等：与 filters/20261011_01 同范式，INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE
-- 动态 DDL，可重复执行；表不存在时跳过。后端服务通过 information_schema 探测列，
-- 未迁移时静默降级（不报错），迁移后即生效——支持「先发版后迁移」。
-- Run with the intended database selected (MySQL 8+).
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @customer_lead_profile_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT  1 AS ord, 'real_name' AS column_name,
                '`real_name` varchar(64) DEFAULT NULL COMMENT ''真实姓名（资料，非实名核验）''' AS column_ddl
        UNION ALL SELECT  2, 'birth_date',
                '`birth_date` date DEFAULT NULL COMMENT ''出生日期；age 由此派生'''
        UNION ALL SELECT  3, 'weight',
                '`weight` int DEFAULT NULL COMMENT ''体重kg'''
        UNION ALL SELECT  4, 'income',
                '`income` varchar(32) DEFAULT NULL COMMENT ''年收入档位'''
        UNION ALL SELECT  5, 'house',
                '`house` varchar(32) DEFAULT NULL COMMENT ''购房情况'''
        UNION ALL SELECT  6, 'car',
                '`car` varchar(32) DEFAULT NULL COMMENT ''购车情况'''
        UNION ALL SELECT  7, 'smoking',
                '`smoking` varchar(32) DEFAULT NULL COMMENT ''吸烟情况'''
        UNION ALL SELECT  8, 'drinking',
                '`drinking` varchar(32) DEFAULT NULL COMMENT ''饮酒情况'''
        UNION ALL SELECT  9, 'ethnicity',
                '`ethnicity` varchar(32) DEFAULT NULL COMMENT ''民族'''
        UNION ALL SELECT 10, 'school',
                '`school` varchar(128) DEFAULT NULL COMMENT ''毕业学校'''
        UNION ALL SELECT 11, 'company',
                '`company` varchar(128) DEFAULT NULL COMMENT ''工作单位'''
        UNION ALL SELECT 12, 'photos',
                '`photos` json DEFAULT NULL COMMENT ''相册图片URL列表'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @customer_lead_profile_ddl = IF(
    @customer_lead_profile_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead` ', @customer_lead_profile_missing)
);
PREPARE customer_lead_profile_cols_stmt FROM @customer_lead_profile_ddl;
EXECUTE customer_lead_profile_cols_stmt;
DEALLOCATE PREPARE customer_lead_profile_cols_stmt;
