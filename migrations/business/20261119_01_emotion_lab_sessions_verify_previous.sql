-- 20261119_01_emotion_lab_sessions 回滚后校验。
-- 返回 0 行表示情感实验室会话表已移除。
SELECT 'emotion_lab_session' AS table_name, 'table' AS object_name, 'table not removed' AS issue
WHERE EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'emotion_lab_session'
);
