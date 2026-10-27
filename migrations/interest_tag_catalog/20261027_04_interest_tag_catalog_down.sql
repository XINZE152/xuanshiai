-- =============================================================================
-- 兴趣标签目录建表 + 播种（down，回滚结构）
--
-- 回滚 20261027_04_interest_tag_catalog_up.sql：删除 interest_tag /
-- interest_tag_category 两张表（会丢弃后台维护的目录改动，恢复为纯常量目录）。
--
-- 注意：回滚后需把读表代码切回常量目录（app/core/tag_catalog.py 会在表缺失时
-- 自动回退到 profile_tags 常量，无需额外改代码）。
--
-- 可重复执行：DROP TABLE IF EXISTS。
-- =============================================================================

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `interest_tag`;
DROP TABLE IF EXISTS `interest_tag_category`;
