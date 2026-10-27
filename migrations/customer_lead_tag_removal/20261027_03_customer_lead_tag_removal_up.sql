-- =============================================================================
-- 客源标签功能下线（DROP customer_lead_tag / customer_lead_tag_relation）（up）
--
-- 背景：客源标签整块功能下线——后台字典 CRUD、线索打标、按标签筛选、下拉 tags、
-- 首页「客户意向统计」标签分布全部移除。兴趣标签目录改由 interest_tag 体系承接。
--
-- 警告：DROP TABLE 会永久丢弃所有已登记的客源标签及其关联数据，不可恢复。
-- 部署顺序：先上线「不再引用这两张表」的后端代码，再执行本迁移，避免旧代码查询
-- 已删除的表导致 500。
--
-- 可重复执行：DROP TABLE IF EXISTS。
-- =============================================================================

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `customer_lead_tag_relation`;
DROP TABLE IF EXISTS `customer_lead_tag`;
