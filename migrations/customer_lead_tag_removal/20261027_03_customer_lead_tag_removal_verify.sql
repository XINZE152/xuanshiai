-- 客源标签功能下线迁移校验（verify）。
-- 在执行 up.sql 后运行：期望两条查询均返回 0 行；任何返回行说明表未被删除。
-- Run with the intended database selected (MySQL 8+).

-- 1) customer_lead_tag 应已不存在：期望 0 行。
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead_tag';

-- 2) customer_lead_tag_relation 应已不存在：期望 0 行。
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_lead_tag_relation';
