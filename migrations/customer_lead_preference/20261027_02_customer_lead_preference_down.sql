-- =============================================================================
-- 客源线索择偶要求暂存表迁移（down）
--
-- 回滚 20261027_02_customer_lead_preference_up.sql：删除 customer_lead_preference 表。
--
-- 警告：DROP TABLE 会永久丢弃所有「未入库线索」已填写但尚未搬运到账号的择偶要求
-- （已入库线索的偏好存在于 user_partner_preference，不受影响）。可重复执行。
-- =============================================================================

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `customer_lead_preference`;
