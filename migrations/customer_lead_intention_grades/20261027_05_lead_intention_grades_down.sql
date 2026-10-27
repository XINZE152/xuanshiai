-- =============================================================================
-- 客源客户意向扩为 A-G 七档（down，回滚）
--
-- 回滚 20261027_05_lead_intention_grades_up.sql：把 1-7 档位折算回粗粒度 1/2/3。
--
-- 有损回滚（精确七档无法无损还原为三档）：
--   1(A) → 1(低)   2/3(B/C) → 1(低)   4/5(D/E) → 2(中)   6/7(F/G) → 3(高)
-- 若回滚，还需同时把读写代码切回 Literal[1,2,3]，否则新写入的 4-7 会被旧代码拒收。
-- =============================================================================

SET NAMES utf8mb4;

UPDATE `customer_lead` SET `intention_level` = 3 WHERE `intention_level` IN (6, 7);
UPDATE `customer_lead` SET `intention_level` = 2 WHERE `intention_level` IN (4, 5);
UPDATE `customer_lead` SET `intention_level` = 1 WHERE `intention_level` IN (2, 3);

UPDATE `customer_lead_follow_up` SET `intention_level` = 3 WHERE `intention_level` IN (6, 7);
UPDATE `customer_lead_follow_up` SET `intention_level` = 2 WHERE `intention_level` IN (4, 5);
UPDATE `customer_lead_follow_up` SET `intention_level` = 1 WHERE `intention_level` IN (2, 3);

ALTER TABLE `customer_lead`
    MODIFY COLUMN `intention_level` tinyint NOT NULL DEFAULT '1'
    COMMENT '1低 2中 3高';

ALTER TABLE `customer_lead_follow_up`
    MODIFY COLUMN `intention_level` tinyint DEFAULT NULL;
