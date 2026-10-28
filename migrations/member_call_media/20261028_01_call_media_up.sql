-- =============================================================================
-- 会员通话记录附件补列迁移（2026-10-28）（up）
--
-- 背景：后台通话记录（member_call_record）需支持附件——图片列表与录音。
-- 对标站 call-records 带 images[] / audio_url；旧表只有文字 remark。
--
-- 幂等：与 filters/20261011_01 同范式，INFORMATION_SCHEMA 检查 + PREPARE/EXECUTE
-- 动态 DDL，可重复执行；表不存在时跳过。后端服务通过 information_schema 探测列，
-- 未迁移时静默降级（读写都不报错），迁移后即生效——支持「先发版后迁移」。
-- Run with the intended database selected (MySQL 8+).
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @member_call_media_missing = (
    SELECT GROUP_CONCAT(CONCAT('ADD COLUMN ', w.column_ddl) ORDER BY w.ord SEPARATOR ', ')
    FROM (
        SELECT 1 AS ord, 'images' AS column_name,
                '`images` json DEFAULT NULL COMMENT ''通话附件图片URL列表''' AS column_ddl
        UNION ALL SELECT 2, 'audio_url',
                '`audio_url` varchar(512) DEFAULT NULL COMMENT ''通话录音URL'''
    ) AS w
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'member_call_record'
    LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
      ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'member_call_record' AND c.COLUMN_NAME = w.column_name
    WHERE c.COLUMN_NAME IS NULL
);
SET @member_call_media_ddl = IF(
    @member_call_media_missing IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `member_call_record` ', @member_call_media_missing)
);
PREPARE member_call_media_cols_stmt FROM @member_call_media_ddl;
EXECUTE member_call_media_cols_stmt;
DEALLOCATE PREPARE member_call_media_cols_stmt;
