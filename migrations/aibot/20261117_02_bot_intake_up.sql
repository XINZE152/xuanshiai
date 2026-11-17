-- BOT 模糊采集快照：选项 id/文案原样落库，与精准字段并存，可供检索与 AI 上下文
ALTER TABLE `user_profile`
  ADD COLUMN `bot_intake_json` JSON NULL COMMENT '恋AI BOT 采集原样：{field:{id,label,source,updated_at}}'
  AFTER `marriage_plan`;
