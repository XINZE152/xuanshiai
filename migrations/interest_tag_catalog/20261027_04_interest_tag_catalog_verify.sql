-- 兴趣标签目录迁移校验（verify）。
-- 在执行 up.sql 后运行：期望两表均存在且行数正确（分类 17、标签 323）。
-- Run with the intended database selected (MySQL 8+).

-- 1) 两表应存在：期望 2 行。
SELECT TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ('interest_tag_category', 'interest_tag');

-- 2) 分类行数应为 17：期望单行 cnt=17。
SELECT COUNT(*) AS cnt FROM `interest_tag_category`;

-- 3) 标签行数应为 323：期望单行 cnt=323。
SELECT COUNT(*) AS cnt FROM `interest_tag`;
