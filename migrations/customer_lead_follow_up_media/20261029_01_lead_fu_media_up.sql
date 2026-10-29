-- =============================================================================
-- 客源线索跟进附件补列迁移（2026-10-29）（up）
--
-- 背景：对标站客源详情「跟进」标签需支持上传图片/录音，与会员侧
-- member_follow_up 的 images(json)/voice_url/voice_duration_sec 对齐。
-- customer_lead_follow_up 旧表只有文字 content，附件无处可存。
--
-- 幂等：与 filters/20261011_01、member_call_media 同范式，INFORMATION_SCHEMA 检查
-- + PREPARE/EXECUTE 动态 DDL，可重复执行；表不存在时跳过。后端服务通过
-- information_schema 探测列，未迁移时静默降级（读写都不报错），迁移后即生效——
-- 支持「先发版后迁移」。
-- Run with the intended database selected (MySQL 8+).
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @lead_fu_media_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'images' AS column_name,
                '`images` json DEFAULT NULL COMMENT ''跟进图片URL列表''' AS column_ddl
        UNION ALL SELECT 2, 'voice_url',
                '`voice_url` varchar(500) DEFAULT NULL COMMENT ''跟进录音地址'''
        UNION ALL SELECT 3, 'voice_duration_sec',
                '`voice_duration_sec` int unsigned DEFAULT NULL COMMENT ''录音时长秒'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead_follow_up'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead_follow_up' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @lead_fu_media_ddl = IF(
    @lead_fu_media_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead_follow_up` ', @lead_fu_media_missing)
);
PREPARE lead_fu_media_cols_stmt FROM @lead_fu_media_ddl;
EXECUTE lead_fu_media_cols_stmt;
DEALLOCATE PREPARE lead_fu_media_cols_stmt;
