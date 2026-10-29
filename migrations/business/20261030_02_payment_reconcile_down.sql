-- 20261030_02_payment_reconcile（down）
-- 删除对账差异与批次表；生产环境默认禁止执行。
-- Run with the intended database selected (MySQL 8+).

SET NAMES utf8mb4;

DROP TABLE IF EXISTS `payment_reconcile_diff`;
DROP TABLE IF EXISTS `payment_reconcile_batch`;
