-- =============================================================================
-- 客源客户意向扩为 A-G 七档（intention_level 1-3 粗粒度 → 1-7）（up）
--
-- 背景：后台「客户意向」筛选前端提供 A-G 七档（A类未接…G类已到店未签约），此前后端
-- 仅存 1低/2中/3高，A/B/C→低、D/E→中、F/G→高 为有损归并，无法按“未接/初步沟通”精确过滤。
-- 本迁移把 customer_lead / customer_lead_follow_up 的 intention_level 语义扩展为 1-7：
--   1 A类未接 / 2 B类初步沟通 / 3 C类深入沟通未缔结 / 4 D类待确定到店时间 /
--   5 E类已确定到店 / 6 F类预约需二邀 / 7 G类已到店未签约
--
-- 存量回填规则（取原粗粒度所属档组的首档，与前端归并口径互逆）：
--   原 1(低) → 1(A)   原 2(中) → 4(D)   原 3(高) → 6(F)
-- 列类型仍是 tinyint，无需改类型；仅回填数据 + 更新列注释。
--
-- 部署顺序：先部署「支持 1-7」的新代码（新代码读旧值 1/2/3 不会报错，只是暂按 A/B/C 显示），
-- 再执行本迁移完成语义回填；两者颠倒都不会 500，但代码先行更安全。
--
-- 幂等：回填源值集合 {2,3} 与目标 {4,6} 不重叠，重复执行第二次匹配不到源值。
-- =============================================================================

SET NAMES utf8mb4;

-- 1) 回填存量粗粒度值（先高→F，再中→D；低→A 即 1→1 无需变动）
UPDATE `customer_lead` SET `intention_level` = 6 WHERE `intention_level` = 3;
UPDATE `customer_lead` SET `intention_level` = 4 WHERE `intention_level` = 2;

UPDATE `customer_lead_follow_up` SET `intention_level` = 6 WHERE `intention_level` = 3;
UPDATE `customer_lead_follow_up` SET `intention_level` = 4 WHERE `intention_level` = 2;

-- 2) 更新列注释为 A-G 七档口径
ALTER TABLE `customer_lead`
    MODIFY COLUMN `intention_level` tinyint NOT NULL DEFAULT '1'
    COMMENT '客户意向 A-G 七档：1 A未接 2 B初步沟通 3 C深入沟通未缔结 4 D待确定到店 5 E已确定到店 6 F预约需二邀 7 G已到店未签约';

ALTER TABLE `customer_lead_follow_up`
    MODIFY COLUMN `intention_level` tinyint DEFAULT NULL
    COMMENT '客户意向 A-G 七档（同 customer_lead.intention_level）';
