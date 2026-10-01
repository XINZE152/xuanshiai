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
- **M6**：`partner_team.level_id`、索引 `idx_partner_team_level(level_id, status)`、`commission_entry.source/remark`、`commission_entry.order_id` 放开为可空并保留字段注释、`partner_level_config` 3 行级别种子（1 初级 / 2 中级 / 3 战略合伙人）。
- **M7**：`offline_activity` 27 列、`activity_signup` 17 列、索引 `idx_activity_signup_promoter` 与 `idx_offline_activity_online`、`merchant_category` 5 行与 `short_video_category` 5 行分类种子。

up 通过 `INFORMATION_SCHEMA` 检查后用 `PREPARE`/`EXECUTE` 执行动态 DDL，可重复执行；表不存在时补列与索引跳过（与 `_ensure_table_columns` / `_ensure_optional_index` 的跳过语义一致）。脚本顶部 `SET SESSION group_concat_max_len = 4194304`，因为 `offline_activity` 27 列拼接的 `ALTER` 语句远超默认 1024 字节上限。种子使用 `INSERT IGNORE`（分别依赖 `uk_partner_level`、`uk_merchant_category_name`、`uk_short_video_category_name` 唯一键），重复执行不会产生重复行。

## 执行方式

本仓库没有通用迁移 runner；按 chat 模块的人工 SQL 迁移方式执行，AI 专用 `scripts/manage_ai_migration.py` 不适用。先确认已选定正确的目标数据库并完成备份，不要对生产环境自动运行 `database_setup_marriage.py`：

1. 执行 `20260916_01_m4_m7_backoffice_columns_up.sql`，再按需执行 `20261004_01_activity_signup_soft_delete_up.sql`（两批互相独立，无先后依赖）。
2. 各自执行对应的 `*_verify.sql`。`20260916_01` 期望：54 列缺失查询返回 0 行，定义明细返回 54 行；`commission_entry.order_id` 恰好 1 行且为 `bigint unsigned`、可空、默认 `NULL`；四个索引 `NON_UNIQUE=1` 且列序正确、无前缀；三个种子幂等依赖唯一键存在且为唯一键；`partner_level_config` 返回 1/2/3 各 1 行，`merchant_category` 与 `short_video_category` 各返回指定名称 5 行。`20261004_01` 期望见下文「二、活动报名软删除迁移」。
3. 再发布依赖这些列的后端版本。开发/测试环境在 `AUTO_INIT_DB=true` 时由 `database_setup_marriage.py` 幂等补齐同一批对象；生产/预发布保持 `AUTO_INIT_DB=false`，只使用显式迁移。

## 回滚

`20260916_01_down.sql` 当前**不会自动回滚**，执行时会以 `SQLSTATE 45000` 阻断并提示缺少迁移前对象 manifest。这是因为旧版本没有保存迁移前状态，无法安全判断某列、索引或 `commission_entry.order_id` 定义是否由本次 up 引入；继续自动 DROP 可能误删已有对象或改写原始字段定义。

如需回滚：

1. 使用发布前保存的对象 manifest，确认本次 up 实际新增的列/索引及 `order_id` 原始定义。
2. 根据 manifest 生成只针对这些差异的定向 DDL。
3. 人工审核并在备份和停写窗口执行；不要直接绕过 `down.sql` 的阻断语义。

`INSERT IGNORE` 写入的种子行不在自动回滚范围内；已有运营配置不得按默认种子值强制覆盖或判定为迁移失败，应结合迁移前记录核对。

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
