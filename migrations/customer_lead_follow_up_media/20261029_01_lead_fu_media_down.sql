-- =============================================================================
-- 客源线索跟进附件补列迁移（2026-10-29）（down）
--
-- 回滚 images/voice_url/voice_duration_sec。幂等：仅删除存在的列，表不存在时跳过。
-- 注意：删列会丢弃已存的跟进附件 URL（磁盘文件不回收），仅在确需回退时使用。
-- Run with the intended database selected (MySQL 8+).
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @lead_fu_media_drop = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', c.COLUMN_NAME, '`') ORDER BY c.COLUMN_NAME SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS c
    JOIN INFORMATION_SCHEMA.TABLES t
      ON t.TABLE_SCHEMA = DATABASE() AND t.TABLE_NAME = 'customer_lead_follow_up'
    WHERE c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = 'customer_lead_follow_up'
      AND c.COLUMN_NAME IN ('images', 'voice_url', 'voice_duration_sec')
);
SET @lead_fu_media_drop_ddl = IF(
    @lead_fu_media_drop IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `customer_lead_follow_up` ', @lead_fu_media_drop)
);
PREPARE lead_fu_media_drop_stmt FROM @lead_fu_media_drop_ddl;
EXECUTE lead_fu_media_drop_stmt;
DEALLOCATE PREPARE lead_fu_media_drop_stmt;
