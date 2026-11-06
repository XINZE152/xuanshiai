-- 20261104_01_good_news (down)
-- 仅删除本次新增的关系表，不动 admin_content_item 中的喜讯内容。

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `good_news_audit`;
DROP TABLE IF EXISTS `good_news_report`;
DROP TABLE IF EXISTS `good_news_blessing`;
DROP TABLE IF EXISTS `good_news_consent`;
DROP TABLE IF EXISTS `good_news_meta`;
