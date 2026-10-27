-- =============================================================================
-- 客源标签功能下线（down，回滚结构）
--
-- 回滚 20261027_03_customer_lead_tag_removal_up.sql：重建 customer_lead_tag /
-- customer_lead_tag_relation 两张空表。
--
-- 警告：DROP 掉的数据无法恢复，本 down 仅回滚表结构；如需恢复标签功能，还需回滚
-- 相应的服务/路由/首页统计代码。
--
-- 可重复执行：CREATE TABLE IF NOT EXISTS。
-- =============================================================================

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `customer_lead_tag` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT, `name` varchar(64) NOT NULL, `color` varchar(16) DEFAULT NULL,
    `enabled` tinyint NOT NULL DEFAULT 1, `sort` int NOT NULL DEFAULT 0,
    PRIMARY KEY (`id`), UNIQUE KEY `uk_customer_lead_tag` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源标签配置';

CREATE TABLE IF NOT EXISTS `customer_lead_tag_relation` (
    `lead_id` bigint unsigned NOT NULL, `tag_id` bigint unsigned NOT NULL,
    PRIMARY KEY (`lead_id`, `tag_id`), KEY `idx_customer_lead_tag` (`tag_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客源标签关联';
