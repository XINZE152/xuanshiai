-- 回滚 BOT 模糊采集列（慎用：会丢 bot 侧原样数据）
ALTER TABLE `user_profile` DROP COLUMN `bot_intake_json`;
