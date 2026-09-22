-- 20260917_01 匹配度算法 v2：回滚 v1 存量快照的失效标记
--
-- 仅用于把代码完整降级回 compatibility-rule-v1 的场合：把本迁移标记过的
-- （invalidated_at 非空且算法版本为 v1 的）行恢复为 ready，并清空失效时间。
--
-- 警告：v1 分数由错误口径算出（集合维度恒不满足、收入金额与档位混算、
-- 学历忽略上界），恢复 ready 会让这些错误分数重新可读。仅当确认要回退到
-- v1 代码时才执行。

UPDATE `ai_compatibility_snapshot`
SET `status` = 'ready',
    `invalidated_at` = NULL
WHERE `algorithm_version` = 'compatibility-rule-v1'
  AND `status` = 'stale';
