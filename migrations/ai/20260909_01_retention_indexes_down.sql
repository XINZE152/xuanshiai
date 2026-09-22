-- Task 9 retention 索引回滚：恢复 20260809_01_hardening 的单列索引，
-- 并删除确定性游标索引。1061/1091 由迁移 runner 忽略，保证可重入。
ALTER TABLE `ai_generation_audit`
  ADD KEY `idx_ai_generation_audit_retention` (`created_at`);

ALTER TABLE `voice_transcript`
  DROP KEY `idx_voice_transcript_retention`;

ALTER TABLE `ai_generation_audit`
  DROP KEY `idx_ai_generation_audit_retention_batch`;

ALTER TABLE `derivation_outbox`
  DROP KEY `idx_derivation_outbox_retention_succeeded`;

ALTER TABLE `derivation_outbox`
  DROP KEY `idx_derivation_outbox_retention_dead_letter`;
