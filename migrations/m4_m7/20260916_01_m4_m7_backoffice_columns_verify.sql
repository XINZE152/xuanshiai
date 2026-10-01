-- M4–M7 后台运营补列迁移校验（verify）。
-- 在执行 up.sql 后运行：任何一条查询的结果与注释中的期望不符，即迁移未完成或被跳过。
-- Run with the intended database selected (MySQL 8+).

SET SESSION group_concat_max_len = 4194304;

-- 1) 补列齐备性：期望返回 0 行；任何返回行都是缺失的列（共 54 列：
--    customer_lead 2 + meeting_record 2 + organization 3 + partner_team 1
--    + commission_entry 2 + offline_activity 27 + activity_signup 17）。
SELECT w.table_name AS missing_table, w.column_name AS missing_column
FROM (
    SELECT 'customer_lead' AS table_name, 'promoter_id' AS column_name
    UNION ALL SELECT 'customer_lead', 'audit_status'
    UNION ALL SELECT 'meeting_record', 'member_visible'
    UNION ALL SELECT 'meeting_record', 'sms_remind'
    UNION ALL SELECT 'organization', 'link_url'
    UNION ALL SELECT 'organization', 'sort_order'
    UNION ALL SELECT 'organization', 'qr_code'
    UNION ALL SELECT 'partner_team', 'level_id'
    UNION ALL SELECT 'commission_entry', 'source'
    UNION ALL SELECT 'commission_entry', 'remark'
    UNION ALL SELECT 'offline_activity', 'organizer'
    UNION ALL SELECT 'offline_activity', 'time_text'
    UNION ALL SELECT 'offline_activity', 'cover_small'
    UNION ALL SELECT 'offline_activity', 'fee_name'
    UNION ALL SELECT 'offline_activity', 'price_male'
    UNION ALL SELECT 'offline_activity', 'price_female'
    UNION ALL SELECT 'offline_activity', 'signup_mode'
    UNION ALL SELECT 'offline_activity', 'require_realname'
    UNION ALL SELECT 'offline_activity', 'limit_mode'
    UNION ALL SELECT 'offline_activity', 'max_male'
    UNION ALL SELECT 'offline_activity', 'max_female'
    UNION ALL SELECT 'offline_activity', 'virtual_people'
    UNION ALL SELECT 'offline_activity', 'virtual_female'
    UNION ALL SELECT 'offline_activity', 'hide_signup_count'
    UNION ALL SELECT 'offline_activity', 'reward_promoter'
    UNION ALL SELECT 'offline_activity', 'reward_service'
    UNION ALL SELECT 'offline_activity', 'reward_partner'
    UNION ALL SELECT 'offline_activity', 'reminder_html'
    UNION ALL SELECT 'offline_activity', 'service_wechat'
    UNION ALL SELECT 'offline_activity', 'service_qr'
    UNION ALL SELECT 'offline_activity', 'virtual_views'
    UNION ALL SELECT 'offline_activity', 'sort_order'
    UNION ALL SELECT 'offline_activity', 'custom_share'
    UNION ALL SELECT 'offline_activity', 'manager_ids'
    UNION ALL SELECT 'offline_activity', 'notify_phones'
    UNION ALL SELECT 'offline_activity', 'online'
    UNION ALL SELECT 'offline_activity', 'audit_status'
    UNION ALL SELECT 'activity_signup', 'gender'
    UNION ALL SELECT 'activity_signup', 'age'
    UNION ALL SELECT 'activity_signup', 'height'
    UNION ALL SELECT 'activity_signup', 'education'
    UNION ALL SELECT 'activity_signup', 'income'
    UNION ALL SELECT 'activity_signup', 'marriage_status'
    UNION ALL SELECT 'activity_signup', 'company'
    UNION ALL SELECT 'activity_signup', 'avatar'
    UNION ALL SELECT 'activity_signup', 'id_card'
    UNION ALL SELECT 'activity_signup', 'is_member'
    UNION ALL SELECT 'activity_signup', 'is_realname'
    UNION ALL SELECT 'activity_signup', 'signup_times'
    UNION ALL SELECT 'activity_signup', 'pay_status'
    UNION ALL SELECT 'activity_signup', 'pay_amount'
    UNION ALL SELECT 'activity_signup', 'checked_in'
    UNION ALL SELECT 'activity_signup', 'in_crm'
    UNION ALL SELECT 'activity_signup', 'promoter_id'
) AS w
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
    ON c.TABLE_SCHEMA = DATABASE()
   AND c.TABLE_NAME = w.table_name
   AND c.COLUMN_NAME = w.column_name
WHERE c.COLUMN_NAME IS NULL
ORDER BY w.table_name, w.column_name;

-- 2) 补列定义明细：期望返回 54 行；逐项核对 COLUMN_TYPE、可空性、默认值，
--    以及字符串列的字符集/排序规则是否符合 up.sql。
SELECT w.table_name, w.column_name,
       c.COLUMN_TYPE, c.IS_NULLABLE, c.COLUMN_DEFAULT,
       c.CHARACTER_SET_NAME, c.COLLATION_NAME
FROM (
    SELECT 'customer_lead' AS table_name, 'promoter_id' AS column_name
    UNION ALL SELECT 'customer_lead', 'audit_status'
    UNION ALL SELECT 'meeting_record', 'member_visible'
    UNION ALL SELECT 'meeting_record', 'sms_remind'
    UNION ALL SELECT 'organization', 'link_url'
    UNION ALL SELECT 'organization', 'sort_order'
    UNION ALL SELECT 'organization', 'qr_code'
    UNION ALL SELECT 'partner_team', 'level_id'
    UNION ALL SELECT 'commission_entry', 'source'
    UNION ALL SELECT 'commission_entry', 'remark'
    UNION ALL SELECT 'offline_activity', 'organizer'
    UNION ALL SELECT 'offline_activity', 'time_text'
    UNION ALL SELECT 'offline_activity', 'cover_small'
    UNION ALL SELECT 'offline_activity', 'fee_name'
    UNION ALL SELECT 'offline_activity', 'price_male'
    UNION ALL SELECT 'offline_activity', 'price_female'
    UNION ALL SELECT 'offline_activity', 'signup_mode'
    UNION ALL SELECT 'offline_activity', 'require_realname'
    UNION ALL SELECT 'offline_activity', 'limit_mode'
    UNION ALL SELECT 'offline_activity', 'max_male'
    UNION ALL SELECT 'offline_activity', 'max_female'
    UNION ALL SELECT 'offline_activity', 'virtual_people'
    UNION ALL SELECT 'offline_activity', 'virtual_female'
    UNION ALL SELECT 'offline_activity', 'hide_signup_count'
    UNION ALL SELECT 'offline_activity', 'reward_promoter'
    UNION ALL SELECT 'offline_activity', 'reward_service'
    UNION ALL SELECT 'offline_activity', 'reward_partner'
    UNION ALL SELECT 'offline_activity', 'reminder_html'
    UNION ALL SELECT 'offline_activity', 'service_wechat'
    UNION ALL SELECT 'offline_activity', 'service_qr'
    UNION ALL SELECT 'offline_activity', 'virtual_views'
    UNION ALL SELECT 'offline_activity', 'sort_order'
    UNION ALL SELECT 'offline_activity', 'custom_share'
    UNION ALL SELECT 'offline_activity', 'manager_ids'
    UNION ALL SELECT 'offline_activity', 'notify_phones'
    UNION ALL SELECT 'offline_activity', 'online'
    UNION ALL SELECT 'offline_activity', 'audit_status'
    UNION ALL SELECT 'activity_signup', 'gender'
    UNION ALL SELECT 'activity_signup', 'age'
    UNION ALL SELECT 'activity_signup', 'height'
    UNION ALL SELECT 'activity_signup', 'education'
    UNION ALL SELECT 'activity_signup', 'income'
    UNION ALL SELECT 'activity_signup', 'marriage_status'
    UNION ALL SELECT 'activity_signup', 'company'
    UNION ALL SELECT 'activity_signup', 'avatar'
    UNION ALL SELECT 'activity_signup', 'id_card'
    UNION ALL SELECT 'activity_signup', 'is_member'
    UNION ALL SELECT 'activity_signup', 'is_realname'
    UNION ALL SELECT 'activity_signup', 'signup_times'
    UNION ALL SELECT 'activity_signup', 'pay_status'
    UNION ALL SELECT 'activity_signup', 'pay_amount'
    UNION ALL SELECT 'activity_signup', 'checked_in'
    UNION ALL SELECT 'activity_signup', 'in_crm'
    UNION ALL SELECT 'activity_signup', 'promoter_id'
) AS w
LEFT JOIN INFORMATION_SCHEMA.COLUMNS c
    ON c.TABLE_SCHEMA = DATABASE()
   AND c.TABLE_NAME = w.table_name
   AND c.COLUMN_NAME = w.column_name
ORDER BY w.table_name, w.column_name;

-- 3) commission_entry.order_id：期望恰好 1 行：bigint unsigned、可空、默认 NULL，
--    并保留“关联订单；后台手工录入时为空”注释。
SELECT IS_NULLABLE, COLUMN_TYPE, COLUMN_DEFAULT,
       CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'commission_entry'
  AND COLUMN_NAME = 'order_id';

-- 4) M4–M7 索引：期望恰好 4 行；NON_UNIQUE=1，且分别为：
--    promoter_id / promoter_id / online,sort_order / level_id,status。
--    prefix_lengths 应全部为 FULL，表示完整列索引而非前缀索引。
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') AS indexed_columns,
       GROUP_CONCAT(
           COALESCE(CAST(SUB_PART AS CHAR), 'FULL')
           ORDER BY SEQ_IN_INDEX SEPARATOR ','
       ) AS prefix_lengths
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND (TABLE_NAME, INDEX_NAME) IN (
        ('customer_lead', 'idx_customer_lead_promoter'),
        ('activity_signup', 'idx_activity_signup_promoter'),
        ('offline_activity', 'idx_offline_activity_online'),
        ('partner_team', 'idx_partner_team_level')
  )
GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
ORDER BY TABLE_NAME, INDEX_NAME;

-- 5) INSERT IGNORE 的幂等依赖：期望恰好 3 行、NON_UNIQUE=0，分别为：
--    uk_partner_level(level_id)、uk_merchant_category_name(name)、
--    uk_short_video_category_name(name)。
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') AS indexed_columns
FROM INFORMATION_SCHEMA.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
  AND (TABLE_NAME, INDEX_NAME) IN (
        ('partner_level_config', 'uk_partner_level'),
        ('merchant_category', 'uk_merchant_category_name'),
        ('short_video_category', 'uk_short_video_category_name')
  )
GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
ORDER BY TABLE_NAME, INDEX_NAME;

-- 6) 合伙红娘级别种子：期望 1/2/3 每级恰好一行，完整配置字段均可读。
--    INSERT IGNORE 会保留已有运营配置；已有值不同不应直接判迁移失败。
--    仅对本次新插入的行核对 up.sql 中的默认种子值。
SELECT level_id, level_name, auto_split_mode, auto_split_rate,
       promote_performance_threshold, promote_member_threshold,
       register_reward_male, register_reward_female, promoter_join_reward,
       consume_commission_mode, consume_commission_rate, share_bonus
FROM partner_level_config
WHERE level_id IN (1, 2, 3)
ORDER BY level_id;

-- 7) 商家分类种子：指定名称各恰好一行；仅对本次新插入的行核对 sort=1…5、status=1。
SELECT name, sort, status
FROM merchant_category
WHERE name IN ('推荐餐饮', '新奇体验', '休闲娱乐', '生活服务', '结婚')
ORDER BY sort;

-- 8) 短视频分类种子：指定名称各恰好一行；仅对本次新插入的行核对 sort=1…5、status=1。
SELECT name, sort, status
FROM short_video_category
WHERE name IN ('关于我们', '脱单干货', '活动瞬间', '优质嘉宾', '直播切片')
ORDER BY sort;
