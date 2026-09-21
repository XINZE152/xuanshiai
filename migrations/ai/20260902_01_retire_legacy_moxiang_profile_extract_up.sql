-- 墨相师实时整理发布前清理：旧 profile_build 已下线，不能让存量 master
-- profile_extract 任务在新 Worker 中继续写确认式草稿。保留 ai_task 行和
-- 审计原因，不删除用户对话、候选、邀请或正式草稿。

UPDATE `ai_profile_session` AS s
INNER JOIN `ai_task` AS t ON JSON_UNQUOTE(
    JSON_EXTRACT(t.`payload_summary`, '$.session_id')
) = s.`session_id`
SET s.`status` = 'draft',
    s.`journey_stage` = 'chatting',
    s.`updated_at` = UTC_TIMESTAMP()
WHERE s.`session_kind` = 'master'
  AND s.`status` = 'extracting'
  AND t.`task_type` = 'profile_extract'
  AND t.`status` IN ('queued', 'leased', 'running', 'retry_wait');

UPDATE `ai_task` AS t
INNER JOIN `ai_profile_session` AS s ON JSON_UNQUOTE(
    JSON_EXTRACT(t.`payload_summary`, '$.session_id')
) = s.`session_id`
SET t.`status` = 'cancelled',
    t.`error_code` = 'AI_LEGACY_MOXIANG_RETIRED',
    t.`error_message` = '旧墨相师确认式抽取已下线，请使用实时整理旅程',
    t.`finished_at` = UTC_TIMESTAMP(),
    t.`updated_at` = UTC_TIMESTAMP()
WHERE t.`task_type` = 'profile_extract'
  AND s.`session_kind` = 'master'
  AND t.`status` IN ('queued', 'leased', 'running', 'retry_wait');
