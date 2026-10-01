-- M4–M7 后台运营补列迁移（down）。
--
-- 本迁移的旧版本没有持久化迁移前对象状态，无法区分：
--   * up.sql 本次新增的列/索引；
--   * 迁移执行前已经存在的列/索引；
--   * commission_entry.order_id 原始是否可空。
--
-- 因此禁止自动 down，避免误删已有列、已有索引或修改原始字段定义。
-- 如需回滚，先根据发布前保存的对象 manifest 生成定向 DDL，再人工审核执行。
SIGNAL SQLSTATE '45000'
SET MESSAGE_TEXT =
    'M4-M7 rollback requires the pre-migration object manifest; automatic down is disabled';
