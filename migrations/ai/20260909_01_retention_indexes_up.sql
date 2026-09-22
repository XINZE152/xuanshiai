-- Task 9: retention 清理扫描的确定性游标索引
-- voice_transcript / ai_generation_audit 按 (created_at, id) 有界批删；
-- derivation_outbox 按 (status, occurred_at, event_id) /
-- (status, dead_letter_at, event_id) 保证 ORDER BY 走索引序读取
-- （见 tests/integration/ai/test_retention_and_outbox.py 的 EXPLAIN 断言）。
-- 索引定义与 app/db/ai_schema.py 的 AI_RETENTION_INDEXES、
-- app/db/derivation_schema.py 的 DERIVATION_RETENTION_INDEXES 保持一致。
-- 20260809_01_hardening 加的单列 idx_ai_generation_audit_retention
-- (created_at) 被 (created_at, id) 完全覆盖，二者并存会让优化器在等价
-- 索引间摇摆，破坏批删的确定性：这里一并替换掉。
ALTER TABLE `voice_transcript`
  ADD KEY `idx_voice_transcript_retention` (`created_at`, `id`);

ALTER TABLE `ai_generation_audit`
  ADD KEY `idx_ai_generation_audit_retention_batch` (`created_at`, `id`);

ALTER TABLE `derivation_outbox`
  ADD KEY `idx_derivation_outbox_retention_succeeded` (`status`, `occurred_at`, `event_id`);

ALTER TABLE `derivation_outbox`
  ADD KEY `idx_derivation_outbox_retention_dead_letter` (`status`, `dead_letter_at`, `event_id`);

ALTER TABLE `ai_generation_audit`
  DROP KEY `idx_ai_generation_audit_retention`;
