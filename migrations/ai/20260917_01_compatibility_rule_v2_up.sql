-- 20260917_01 匹配度算法 v2：失效 v1 存量快照
--
-- 背景（修复清单 §3.3）：compatibility-rule-v1 的分数由错误口径算出——
--   * 集合类维度（理想型 marriage_status / relationship_goal / city_code）
--     经 _as_str 比较恒判不满足；
--   * 理想型 income_band 的金额口径与个人 0-6 档位混算；
--   * _score_education 忽略 max 上界。
-- 代码已升版到 compatibility-rule-v2，读路径按 algorithm_version 精确过滤
-- （app/services/ai/compatibility.py 的 _load_latest_snapshot），因此 v1 行
-- 不再被读到。本迁移把这些行显式标记为 stale，使数据状态与代码语义一致，
-- 便于后台审计与后续清理。
--
-- 纪律：只改 status / invalidated_at，不删除行、不改分数、不动 legacy
-- match_score。幂等：重复执行不会影响已 stale 的行。
--
-- 回滚说明：down 脚本仅把本迁移标记的 v1 行恢复为 ready —— 但 v1 分数本身
-- 已被证伪，恢复只用于完整降级到 v1 代码的场合。

UPDATE `ai_compatibility_snapshot`
SET `status` = 'stale',
    `invalidated_at` = UTC_TIMESTAMP()
WHERE `algorithm_version` = 'compatibility-rule-v1'
  AND `status` = 'ready';
