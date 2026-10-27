-- =============================================================================
-- 客源线索择偶要求独立暂存表（未入库可维护，入库后搬运至账号）（up）
--
-- 背景：客源详情「择偶要求」tab 此前复用 C 端 user_partner_preference（方案①），
-- 偏好只能挂在入库后生成的账号（converted_user_id）上，未入库线索无法维护
-- （GET 返回 editable=false、PUT 返回 409）。现新建独立暂存表
-- customer_lead_preference（字段口径与 C 端 PreferenceUpdateRequest/Response 对齐），
-- 未入库线索可直接写本表；线索入库(convert)时由服务把本表该行复制到新账号的
-- user_partner_preference，已入库后后台 tab 改读账号那份。
--
-- 隔离性：本表以 lead_id 为唯一键，不写 C 端核心表，不影响任何按 user_id join
-- user_partner_preference 的推荐/完整度/画像口径。
--
-- 可重复执行：CREATE TABLE IF NOT EXISTS。
-- =============================================================================

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `customer_lead_preference` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT,
    `lead_id` bigint unsigned NOT NULL COMMENT '客源线索 customer_lead.id（未入库暂存键，入库后搬运至账号）',
    `age_min` tinyint unsigned DEFAULT NULL,
    `age_max` tinyint unsigned DEFAULT NULL,
    `height_min` smallint unsigned DEFAULT NULL,
    `height_max` smallint unsigned DEFAULT NULL,
    `education_min` tinyint unsigned DEFAULT NULL,
    `income_min` decimal(10,2) DEFAULT NULL,
    `marriage_status` tinyint DEFAULT NULL COMMENT '婚姻状况要求',
    `preferred_province_code` varchar(32) DEFAULT NULL,
    `preferred_city_codes` json DEFAULT NULL COMMENT '期望城市编码列表',
    `accept_long_distance` tinyint NOT NULL DEFAULT '0',
    `accept_cross_province` tinyint NOT NULL DEFAULT '0',
    `housing_requirement` tinyint DEFAULT NULL COMMENT '0不限 1有房 2无房',
    `smoking_requirement` tinyint DEFAULT NULL COMMENT '0不限 1不抽烟 2可接受',
    `drinking_requirement` tinyint DEFAULT NULL COMMENT '0不限 1不饮酒 2可接受',
    `dating_goal` varchar(32) DEFAULT NULL COMMENT '交友目标：倾向恋爱/倾向结婚',
    `meeting_pace` varchar(64) DEFAULT NULL COMMENT '见面节奏',
    `children_intention` varchar(64) DEFAULT NULL COMMENT '生育意愿',
    `extra_requirement` varchar(1000) DEFAULT NULL,
    `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
    `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_lead_preference_lead` (`lead_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客源线索择偶要求（未入库暂存，入库后搬运至 user_partner_preference）';
