-- =============================================================================
-- 会员通话记录附件补列迁移（2026-10-28）（down）
--
-- 回滚 20261028_01_call_media_up.sql：删除 member_call_record 的 images / audio_url 列。
--
-- 警告：DROP COLUMN 会永久丢弃已上传的通话附件引用，执行前必须先备份。
-- 可重复执行：列不存在时跳过。
-- =============================================================================

SET SESSION group_concat_max_len = 4194304;
SET NAMES utf8mb4;

SET @member_call_media_drop = (
    SELECT GROUP_CONCAT(CONCAT('DROP COLUMN `', c.COLUMN_NAME, '`') ORDER BY c.ORDINATE_POSITION SEPARATOR ', ')
    FROM INFORMATION_SCHEMA.COLUMNS c
    WHERE c.TABLE_SCHEMA = DATABASE()
      AND c.TABLE_NAME = 'member_call_record'
      AND c.COLUMN_NAME IN ('images', 'audio_url')
);
SET @member_call_media_down_ddl = IF(
    @member_call_media_drop IS NULL,
    'SELECT 1',
    CONCAT('ALTER TABLE `member_call_record` ', @member_call_media_drop)
);
PREPARE member_call_media_down_stmt FROM @member_call_media_down_ddl;
EXECUTE member_call_media_down_stmt;
DEALLOCATE PREPARE member_call_media_down_stmt;
