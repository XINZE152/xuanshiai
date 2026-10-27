-- 客源客户意向扩为 A-G 七档迁移校验（verify）。
-- 在执行 up.sql 后运行。Run with the intended database selected (MySQL 8+).

-- 1) 列注释应已更新为 A-G 七档口径：期望单行，且 COLUMN_COMMENT 含「A-G 七档」。
SELECT TABLE_NAME, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('customer_lead', 'customer_lead_follow_up')
  AND COLUMN_NAME = 'intention_level';

-- 2) 存量值应全部落在 1-7：期望 0 行（有任何越界值说明回填/写入异常）。
SELECT id, intention_level
FROM `customer_lead`
WHERE `intention_level` NOT BETWEEN 1 AND 7;

SELECT id, intention_level
FROM `customer_lead_follow_up`
WHERE `intention_level` IS NOT NULL AND `intention_level` NOT BETWEEN 1 AND 7;

-- 3) 分布快照（人工核对回填结果：老数据只应出现 1/4/6，新写入可出现 1-7）。
SELECT `intention_level`, COUNT(*) AS cnt
FROM `customer_lead`
GROUP BY `intention_level`
ORDER BY `intention_level`;
