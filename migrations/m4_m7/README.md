# M4–M7 迁移

本目录当前两批迁移，均需按「先迁移、后发版」顺序执行，且只对 `AUTO_INIT_DB=false` 的生产/预发布库是必需的：

| 迁移 | 内容 | 对应改动 |
| --- | --- | --- |
| `20260916_01_m4_m7_backoffice_columns_*` | M4–M7 后台运营补列与种子 | 见下文「一、后台运营补列」 |
| `20261004_01_activity_signup_soft_delete_*` | `activity_signup` 软删除两列 + 索引 | 见下文「二、活动报名软删除」 |

---

## 一、M4–M7 后台运营补列迁移

`20260916_01_m4_m7_backoffice_columns_up.sql` 逐条镜像 `database_setup_marriage.py` 的 `_ensure_m4_columns` / `_ensure_m5_columns` / `_ensure_m6_columns` / `_ensure_m7_columns`（`database_setup_marriage.py:3390-3510`），为 `AUTO_INIT_DB=false` 的生产/预发布库补齐提交 `8ec8ff1` 事故中从未创建成功的后台运营列（事故根因与影响见 `tests/test_m4_m7_column_migration.py` 模块文档）。

覆盖范围：

- **M4**：`customer_lead.promoter_id/audit_status`、`meeting_record.member_visible/sms_remind`、索引 `idx_customer_lead_promoter`。
- **M5**：`organization.link_url/sort_order/qr_code`。
- **M6**：`partner_team.level_id`、`commission_entry.source/remark`、`commission_entry.order_id` 放开为可空、`partner_level_config` 3 行级别种子（1 初级 / 2 中级 / 3 战略合伙人）。
- **M7**：`offline_activity` 27 列、`activity_signup` 17 列、索引 `idx_activity_signup_promoter` 与 `idx_offline_activity_online`、`merchant_category` 5 行与 `short_video_category` 5 行分类种子。

up/down 通过 `INFORMATION_SCHEMA` 检查后用 `PREPARE`/`EXECUTE` 执行动态 DDL，可重复执行；表不存在时补列与索引跳过（与 `_ensure_table_columns` / `_ensure_optional_index` 的跳过语义一致）。脚本顶部 `SET SESSION group_concat_max_len = 4194304`，因为 `offline_activity` 27 列拼接的 `ALTER` 语句远超默认 1024 字节上限。种子使用 `INSERT IGNORE`（分别依赖 `uk_partner_level`、`uk_merchant_category_name`、`uk_short_video_category_name` 唯一键），重复执行不会产生重复行。

## 执行方式

本仓库没有通用迁移 runner；按 chat 模块的人工 SQL 迁移方式执行，AI 专用 `scripts/manage_ai_migration.py` 不适用。先确认已选定正确的目标数据库并完成备份，不要对生产环境自动运行 `database_setup_marriage.py`：

1. 执行 `20260916_01_m4_m7_backoffice_columns_up.sql`，再按需执行 `20261004_01_activity_signup_soft_delete_up.sql`（两批互相独立，无先后依赖）。
2. 各自执行对应的 `*_verify.sql`。`20260916_01` 期望：补列齐备性查询返回 0 行；`commission_entry.order_id` 为 `IS_NULLABLE=YES`；三个索引 `NON_UNIQUE=1` 且列序正确；`partner_level_config` 返回 3 行（level_id 1/2/3），`merchant_category` 与 `short_video_category` 各返回 5 行。
3. 再发布依赖这些列的后端版本。开发/测试环境在 `AUTO_INIT_DB=true` 时由 `database_setup_marriage.py` 幂等补齐同一批对象；生产/预发布保持 `AUTO_INIT_DB=false`，只使用显式迁移。

## 回滚

回滚前停止写入这些列的后端版本并确认备份，再执行对应的 `*_down.sql`：

- `20260916_01_*_down.sql` 幂等删除三个索引并删除 up 新增的全部补列，**会永久删除这些列已写入的数据**（如 `customer_lead.audit_status`、`offline_activity.online/audit_status`、`activity_signup` 报名资料快照等），只能在放弃这些数据后使用；down 可重复执行。
- `commission_entry.order_id` 恢复 `NOT NULL` 仅当表中不存在 `order_id IS NULL` 的行时执行；存在 NULL 数据（后台手工录入分成）时脚本跳过该步并输出 `rollback_blocked` 提示，需先处理这批数据。
- `INSERT IGNORE` 写入的种子行不在回滚范围内，down 不会删除 `partner_level_config` / `merchant_category` / `short_video_category` 的种子数据。

---

## 二、活动报名软删除迁移（2026-10-04）

`20261004_01_activity_signup_soft_delete_up.sql` 为 `activity_signup` 补 `deleted_at` / `deleted_by` 两列与索引 `idx_activity_signup_deleted`，逐条镜像 `database_setup_marriage.py` 的 `_ensure_m7_columns`（`:3495-3499`）。

**背景**：报名删除原为物理删除且无审计，而报名记录含支付金额与签到状态，删除后无法追溯、无法支撑退款对账。应用层已同步改为软删除 + `business_audit_log` 快照（`app/api/routes/activity_admin.py`），接口契约见 `docs/api/m7-activity.md` 2.8 / 3.8。

**幂等性**：与 `20260916_01` 相同，`INFORMATION_SCHEMA` 检查 + `PREPARE`/`EXECUTE` 动态 DDL，可重复执行；`activity_signup` 表不存在时静默跳过。

**执行与校验**：

1. 执行 `20261004_01_activity_signup_soft_delete_up.sql`。
2. 执行 `20261004_01_activity_signup_soft_delete_verify.sql`，期望：第 1 项缺失列查询返回 0 行；第 2 项返回 2 行且两项均 `IS_NULLABLE=YES`（`deleted_at` 为 `datetime`、`deleted_by` 为 `bigint unsigned`）；第 3 项返回 1 行且 `NON_UNIQUE=1`、`COLUMN_NAME=deleted_at`；第 4 项非法软删除标记（只填 `deleted_by` 或只填 `deleted_at`）返回 0 行。
3. 再发布依赖软删除的后端版本。开发/测试在 `AUTO_INIT_DB=true` 时由 `_ensure_m7_columns` 自动补齐，无需手工执行。

**顺序要求**：必须**先迁移、后发版**。新版本代码的报名查询全部带 `deleted_at IS NULL`，列不存在会直接 SQL 报错。

**回滚**：`20261004_01_activity_signup_soft_delete_down.sql` 删除索引与两列，**会永久丢失软删除标记（含 `deleted_by` 操作人留痕）**，已软删的报名记录在回滚后重新变为"存在"状态。仅在放弃这批留痕时使用；down 可重复执行，未写入过数据的库可安全执行。

## 与自动化测试的对应

- `tests/test_m4_m7_column_migration.py`：覆盖 `20260916_01` 的 up/down/verify 与 `_ensure_*` 的一致性。
- `tests/test_activity_signup_soft_delete.py`：覆盖 `20261004_01` 的 up/down/verify 结构、软删除列在 `_ensure_m7_columns` 中的齐备性，以及应用层软删除与审计行为。

