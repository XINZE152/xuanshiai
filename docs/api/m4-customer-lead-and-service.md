# 客源线索 & 会员服务 接口契约（M4）

> 覆盖 5 个后端模块（共 **38 端点**）：
> 1. **客源线索**：`app/api/routes/customer_leads_admin.py`（16 端点）
> 2. **会员服务·约见/约会**：`app/api/routes/meeting.py` 内 `admin_router`（13 端点）
> 3. **会员服务·红娘牵线**：`app/api/routes/matchmaker_crm_admin.py` 内 `match_records`（2 端点）
> 4. **会员服务·服务申请**：`app/api/routes/matchmaker.py` 内 `admin_router` 内 `service-requests`（2 端点）
> 5. **会员服务·推广管理**：`app/api/routes/promotion_order_admin.py`（5 端点）

所有端点均按 `PROJECT_RULES.md` 2.1.1 的统一模板撰写。本文档随代码演进同步维护。

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：**红娘后台独立 Token**（非 C 端 token）
- 请求头：`Authorization: Bearer <access-token>`
- 鉴权函数：`get_current_matchmaker_admin`
- 所有 admin 端点未登录 → **401**；权限点缺失 → **403**

### 1.2 权限点

| 路径前缀 | 读取权限 | 写入权限 |
|---|---|---|
| `/admin/customer-leads/*` | `customer_lead.manage`（读写同点，路由内显式 `require`） | 同左 |
| `/admin/matchmaker/service-requests/*` | `matchmaker.service.read` | `matchmaker.service.manage` |
| `/admin/matchmaker/match-records/*` | `matchmaker.service.read` | `matchmaker.service.manage` |
| `/admin/matchmaker/meetings/*`（GET） | `meeting.read` | `meeting.write`（非 GET） |
| `/admin/matchmaker/meetings/{id}/feedback` | `meeting.feedback.read` | — |
| `/admin/promotion-orders/*` | `matchmaker.service.read` | `matchmaker.service.manage` |
| `/admin/configs/{namespace}`（客源功能配置页） | `platform.config.read` | `platform.config.write` |

依赖文件：`app/api/dependencies.py:_matchmaker_admin_permission(request)` 按路径子串自动映射（`/promotion-orders` 已加入映射表）。

### 1.3 响应与异常

- 成功响应**不包裹** `data` 字段
- 错误统一 `HTTPException(status_code, detail="...")` → 响应体 `{"detail": "..."}`，**无业务错误码字段**
- 分页：`items / page / page_size / total / has_more`
- 金额 Decimal 一律序列化为 **字符串**（如 `"12.30"`）
- 时间字段：MySQL `UTC_TIMESTAMP()` 写入，Python 端读取后按 ISO 8601 返回

### 1.4 数据库

**M4 收尾新增**：

| 变更 | 对象 | 说明 |
|---|---|---|
| 新表 | `promotion_order` | 推广服务订单（推广管理页数据源）：`order_no`(uk) / `user_id` / `product_name` / `amount` / `pay_status` / `pay_method` / `status` / `remark` / `paid_at` |
| 补列 | `customer_lead.promoter_id` | `bigint unsigned NULL`，推广红娘用户ID（+ 索引 `idx_customer_lead_promoter`） |
| 补列 | `customer_lead.audit_status` | `varchar(16) NOT NULL DEFAULT 'active'`，`active` 有效 / `pending` 待核 |
| 补列 | `meeting_record.member_visible` | `tinyint NOT NULL DEFAULT 1`，会员端是否可见 |
| 补列 | `meeting_record.sms_remind` | `tinyint NOT NULL DEFAULT 1`，是否发送约会短信提醒 |

落点：`app/db/business_schema.py`（CREATE TABLE）+ `database_setup_marriage.py::DatabaseManager._ensure_m4_columns`（旧库幂等补列，已注册进迁移链）。

其余端点复用已有表：

- `customer_lead` / `customer_lead_follow_up` / `customer_lead_abandonment` / `customer_lead_tag` / `customer_lead_tag_relation`
- `meeting_request` / `meeting_record` / `meeting_feedback`
- `match_apply`（匹配申请 = 红娘牵线记录）
- `matchmaker_service_quota`（服务配额）
- `admin_config_snapshot`（`tools_customer_leads` 配置域）

---

## 二、客源线索（16 端点）

### 2.1 列表 `GET /api/v1/admin/customer-leads`

**query 参数**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | 1-1000 | 页码 |
| `page_size` | int | 20 | 1-100 | 每页条数 |
| `status` | string \| null | — | `NEW/CONTACTED/INTENDED/CONVERTED/LOST/CLOSED` | 线索状态 |
| `source` | string \| null | — | ≤64 | 来源 |
| `matchmaker_id` | int \| null | — | ≥1 | 所属服务红娘 |
| `search` | string \| null | — | ≤64 | 关键字（姓名/手机/微信，LIKE） |

**返回** `CustomerLeadPage {items,page,page_size,total,has_more}`，item 字段：
`id` `name` `phone` `wechat` `source` `intention_level(1低/2中/3高)` `status` `audit_status(active有效/pending待核)` `matchmaker_id` `organization_id` `promoter_id（推广红娘）` `tags: string[]（标签名数组）` `next_follow_at` `converted_user_id` `remark` `created_by` `created_at` `updated_at`。

### 2.2 录入 `POST /api/v1/admin/customer-leads`

**body** `CustomerLeadCreate`：必填 `name(1-128)` + `source(1-64)`，`phone`/`wechat` **至少提供一个**；可选 `intention_level(1/2/3,默认1)` `remark(≤2000)` `promoter_id` `audit_status(默认 active)` `tags(string[] 或逗号分隔字符串)`。

**返回**：201 `CustomerLead`。

**非法示例**：`{"name":"张三","source":"抖音"}` → 422 `"phone 或 wechat 至少提供一个"`。

### 2.3 统计 `GET /api/v1/admin/customer-leads/statistics`

无参数。返回 `CustomerLeadStatistics {total,new_count,contacted_count,intended_count,converted_count,lost_count}`。

### 2.4 弃海池 `GET /api/v1/admin/customer-leads/abandoned`

无参数。返回 `list[CustomerLeadAbandonment]`，仅 `restored_at IS NULL` 的记录。

### 2.5 弃海记录 `GET /api/v1/admin/customer-leads/abandonments`

无参数。返回 `list[CustomerLeadAbandonment]`，全部弃海流水（含已恢复）。

### 2.6 详情 `GET /admin/customer-leads/{lead_id}`

**path**：`lead_id`(≥1)。

**返回**：`CustomerLead`。

### 2.7 编辑 `PATCH /admin/customer-leads/{lead_id}`

**body** `CustomerLeadUpdate`：全可选（`name/phone/wechat/intention_level/status/remark/next_follow_at/promoter_id/audit_status`），至少一项；**不支持**改 tags（走录入或后续标签端点）。

**业务规则**：目标状态为有效线索时，联系方式不得与其它有效线索重复（`uk_customer_lead_active_phone/active_wechat`）；置为 `LOST/CLOSED` 后生成列自动置空。

### 2.8 跟进记录 `GET /admin/customer-leads/{lead_id}/follow-ups`

**query**：`page/page_size`。返回 `list[CustomerLeadFollowUp]`（倒序）。

### 2.9 批量导入（JSON）`POST /admin/customer-leads/batch-import`

**body** `CustomerLeadBatchImportRequest {rows: list[CustomerLeadImportRow](1-1000), dup_mode: 'skip'|'append'}`；每行 `name(必填)/phone/wechat/source(必填)/intention_level/remark`。

**返回**：`CustomerLeadBatchImportResult {created, skipped, failed, errors[]}`。

**业务规则**：
- `phone` 与 `wechat` 都为空 → 该行 `failed`
- `dup_mode=skip`：联系方式命中的有效线索（`status NOT IN ('LOST','CLOSED')`）→ `skipped`
- 单行异常不阻断整批，`errors` 最多回传 50 条
- 行号从 **2** 开始（Excel 首行为表头）

### 2.10 新增跟进 `POST /admin/customer-leads/{lead_id}/follow-ups`

**body** `CustomerLeadFollowUpCreate`：`method(PHONE/WECHAT/VISIT/OTHER)` `content(1-2000)`，可选 `intention_level(1-3)` `next_follow_at`。

**返回**：201 `CustomerLeadFollowUp`。附带把 `NEW` 状态的线索推进为 `CONTACTED`。

### 2.11 分配 `PATCH /admin/customer-leads/{lead_id}/assignment`

**body** `{matchmaker_id?: int, organization_id?: int}`（至少一个）。校验服务红娘 `status=1`、门店 `org_type='store' AND status=1`。

**业务规则**：线索必须在 status=0（待分配）。已分配/已到店等 → 409。

### 2.12 弃海 `POST /admin/customer-leads/{lead_id}/abandon`

**body** `{reason: string(1-500)}`。

**返回**：201 `CustomerLeadAbandonment {id,lead_id,reason,abandoned_by,abandoned_at,restored_by,restored_at,restore_reason}`。

**业务规则**：`CONVERTED`（已入库）/ `CLOSED`（已关闭）→ 409「已入库或已关闭的客源不能弃海」；已在弃海池 → 409。弃海会置 `status='LOST'`、清空 `matchmaker_id` 与 `next_follow_at`。

### 2.13 恢复 `POST /admin/customer-leads/{lead_id}/restore`

**body** `{reason: string(1-500)}`。

**业务规则**：不在弃海池 → 409；恢复前校验联系方式未被其它有效线索占用（否则 409）；恢复后 `status='NEW'`。

### 2.14 下拉字典 `GET /admin/customer-leads/options` ✅ M4 收尾新增

无参数。返回 `CustomerLeadOptions {sources[], matchmakers[], promoters[], tags[]}`，每项 `{value,label}`：

| 字典 | 数据源 |
|---|---|
| `sources` | `customer_lead.source` DISTINCT |
| `matchmakers` | `user_matchmaker_apply(application_type='service_matchmaker', status=1)` JOIN `user_role(role_code='service_matchmaker', status=1)` |
| `promoters` | `user_matchmaker_apply(application_type='promoter', status=1)` |
| `tags` | `customer_lead_tag(enabled=1)` 按 `sort,id` |

### 2.15 下载导入模板 `GET /admin/customer-leads/import-template` ✅ M4 收尾新增

无参数，返回 `.xlsx` 二进制（`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，`Content-Disposition: attachment; filename="customer-lead-import-template.xlsx"`）。

模板表头（顺序固定）：`姓名 | 手机号 | 微信号 | 来源 | 意向程度 | 备注`，第 2 行为示例行（导入前请删除）。生成实现 `app/services/customer_lead_admin.py::build_import_template`（openpyxl）。

### 2.16 文件导入 `POST /admin/customer-leads/import` ✅ M4 收尾新增

`multipart/form-data`：

| 字段 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|
| `file` | UploadFile | ✅ | — | `.xlsx` | 按模板填写的 Excel |
| `audit_status` | string | — | `active` | `^(active\|pending)$` | 本次导入的审核状态 |
| `default_source` | string | — | `批量导入` | ≤64 | 留空/不匹配时的默认来源 |
| `default_matchmaker_id` | int \| null | — | — | ≥1 | 默认分派红娘 |
| `default_promoter_id` | int \| null | — | — | ≥1 | 默认推广红娘 |
| `default_tags` | string | — | `""` | ≤255 | 默认标签，英文逗号分隔（标签名不存在会自动补建） |
| `dup_mode` | string | — | `skip` | `^(skip\|append)$` | 重复处理 |

**返回** `CustomerLeadImportSummary {created, skipped, failed, total, errors[]}`（比 `batch-import` 多一个 `total` = 解析出的总行数）。

**错误**：

| HTTP | 触发 | detail |
|---|---|---|
| 400 | 上传 `.xls` | 暂不支持 .xls 旧格式，请用 Excel 另存为 .xlsx 后重新上传 |
| 400 | 非 Excel / 文件损坏 | Excel 解析失败：{原因} |
| 400 | 缺少「姓名」或「手机号」列 | 模板表头缺少「姓名」或「手机号」列，请使用下载的模板填写 |
| 400 | 存在姓名空行 | 存在姓名为空的数据行，请补齐后再导入 |
| 400 | 无有效数据行 | 文件中没有可导入的数据行 |

**解析规则**：表头按关键字包含匹配（`姓名/昵称/name`、`手机/电话/phone`、`微信/wechat`、`来源/source`、`意向/intention`、`备注/remark`），列顺序可变；意向程度接受 `低/中/高` 与 `1/2/3`，无法识别按 `1`（低）；整行空白跳过。

### 2.17 后端代码位置

- 路由：`app/api/routes/customer_leads_admin.py`（16 端点；`/import-template`、`/options`、`/import` 均声明在 `/{lead_id}` **之前**）
- Schema：`app/schemas/customer_lead_admin.py`
- Service：`app/services/customer_lead_admin.py`（`list_leads`/`create_lead`/`update_lead`/`get_lead`/`assign_lead`/`add_follow_up`/`list_follow_ups`/`lead_statistics`/`abandon_lead`/`restore_lead`/`list_abandonments`/`batch_import_leads`/`_insert_leads`/`import_leads_file`/`build_import_template`/`parse_import_file`/`lead_options`/`_sync_tags`）

---

## 三、会员服务·约见/约会（13 端点）

> **入口路径**：`/api/v1/admin/matchmaker/meetings/...`
> **权限**：读 `meeting.read` / 写 `meeting.write`；反馈详情读 `meeting.feedback.read`

### 3.1 约见申请列表 `GET /admin/matchmaker/meetings/requests`

**query**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | — |
| `page_size` | int | 20 | 1-100 | — |
| `status` | string \| null | — | ≤32 | 精确状态：`SUBMITTED` / `CONTACTED` / `ACCEPTED` / `DECLINED` / `CLOSED` |
| `status_group` | string \| null | — | `^(pending\|done)$` | ✅ M4 收尾新增。`pending`=SUBMITTED/CONTACTED，`done`=ACCEPTED/DECLINED/CLOSED；与 `status` **互斥**（给 `status` 时忽略分组） |
| `search_value` | string \| null | — | ≤64 | 昵称 LIKE，或 ID 精确（纯数字时同时按 `user_id`/`target_user_id` 匹配） |
| `matchmaker_id` | int \| null | — | ≥1 | 红娘筛选 |
| `from_date` / `to_date` | string \| null | — | ≤32 | 按 `created_at`，`to_date` 为闭区间（内部 `+1 DAY` 左闭右开） |

**返回** `MeetingRequestAdminPage`，item = `MeetingRequestResponse`：`id` `user_id` `target_user_id` `service_id` `matchmaker_id` `organization_id` `status` `note` `created_at` `updated_at` `user_nickname` `user_member_code` `target_nickname` `target_member_code` `matchmaker_name`。

> 前端「提交人」「想问约」两列分别取 `user_nickname/user_member_code` 与 `target_nickname/target_member_code`。

### 3.2 约见申请详情/处理 `PATCH /admin/matchmaker/meetings/requests/{request_id}`

**body** `MeetingStatusUpdate`：`status`(`CONTACTED`/`ACCEPTED`/`DECLINED`/`CLOSED`) `reason?`(≤255)。

**业务规则**：`ACCEPTED` 会向被约见人发通知；`DECLINED` 向申请人发通知并带 `reason`，同时写 `business_audit_log(action='meeting_request.review')`。

### 3.3 安排约会 `POST /admin/matchmaker/meetings/requests/{request_id}/schedule`

**前置**：① 约见申请 `status='ACCEPTED'`，否则 409「只有双方接受的约见申请才能安排约会」；② 当前后台账号必须绑定红娘用户（`admin.account.matchmaker_user_id` 非 None），否则 409。

**body** `MeetingScheduleCreate`：

| 字段 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|
| `organizer_id` | int | ✅ | — | 约见服务红娘用户ID |
| `organization_id` | int \| null | — | — | 门店 |
| `scheduled_at` | datetime | ✅ | — | 见面时间 |
| `location` | string(1-255) | ✅ | — | 见面地点 |
| `member_visible` | bool | — | `true` | ✅ M4 收尾：会员端是否可见 |
| `sms_remind` | bool | — | `true` | ✅ M4 收尾：是否发约会短信提醒 |

**返回**：201 `MeetingRecordResponse`。

### 3.4 约会记录列表 `GET /admin/matchmaker/meetings`

**query**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` / `page_size` | int | 1 / 20 | 1-100 | — |
| `status` | string \| null | — | ≤32 | `SCHEDULED`/`REMINDED`/`CHECKED_IN`/`COMPLETED`/`CANCELLED`/`NO_SHOW` |
| `search` | string \| null | — | ≤64 | ✅ M4 收尾：男方/女方昵称、手机，或双方 ID 精确 |
| `organizer_id` | int \| null | — | ≥1 | ✅ M4 收尾：本次约见服务红娘 |
| `met` | string \| null | — | `^(met\|wait)$` | ✅ M4 收尾：`met`=CHECKED_IN/COMPLETED，`wait`=SCHEDULED/REMINDED |
| `from_date` / `to_date` | string \| null | — | `^\d{4}-\d{2}-\d{2}$` | ✅ M4 收尾：按 `scheduled_at` |

**返回** `MeetingRecordAdminPage`，item = `MeetingRecordResponse`：

`id` `request_id` `organizer_id` `organization_id` `scheduled_at` `location` `status` `cancel_reason` `member_visible` `sms_remind` `created_at` `updated_at` `from_user_id` `from_nickname` `to_user_id` `to_nickname` `organizer_name` `feedback_count`。

### 3.5 约会详情 `GET /admin/matchmaker/meetings/{meeting_id}`

**path**：`meeting_id`(≥1)。**返回** `MeetingRecordResponse`（字段同 3.4，含 JOIN 出的双方昵称与反馈条数）。

### 3.6 约会更新 `PATCH /admin/matchmaker/meetings/{meeting_id}`

**body** `MeetingRecordAdminUpdate`：全可选 —— `scheduled_at` / `location`(1-255) / `status`(6 枚举) / `cancel_reason`(≤255) / `member_visible` / `sms_remind`。

**业务规则**：已 `CANCELLED` 不能恢复（409）；置 `CANCELLED` 必须带 `cancel_reason`（422）；写 `business_audit_log(action='meeting.update')`。

### 3.7 约会反馈列表 `GET /admin/matchmaker/meetings/{meeting_id}/feedback`

**权限** `meeting.feedback.read`。

**返回** `list[MeetingFeedbackAdminItem]`，字段：`id` `meeting_id` `user_id` `target_rating(1-5)` `matchmaker_rating(1-5)` `continue_intent(1/2/3)` `private_feedback` `created_at`。

### 3.8 管理页面下拉字典 `GET /admin/matchmaker/meetings/options` ✅ M4 新增

**返回**：

```json
{
  "matchmakers": [{"id": 1, "nickname": "芸希老师", "avatar": null, "phone": "13800000000"}],
  "candidates":  [{"id": 2, "nickname": "Alice", "avatar": null, "phone": "13900000000", "gender": 2, "birthday": "1995-01-01"}],
  "request_status": [
    {"value": "SUBMITTED", "label": "待联系"},
    {"value": "CONTACTED", "label": "已联系"},
    {"value": "ACCEPTED",  "label": "已接受"},
    {"value": "DECLINED",  "label": "已拒绝"},
    {"value": "CLOSED",    "label": "已关闭"}
  ],
  "record_status": [
    {"value": "SCHEDULED",  "label": "已安排"},
    {"value": "REMINDED",   "label": "已提醒"},
    {"value": "CHECKED_IN", "label": "已签到"},
    {"value": "COMPLETED",  "label": "已完成"},
    {"value": "CANCELLED",  "label": "已取消"},
    {"value": "NO_SHOW",    "label": "未到场"}
  ]
}
```

**业务规则**：
- `matchmakers` 来自 `users JOIN user_role` 中角色为 matchmaker 的在职用户
- `candidates` 来自 `users WHERE status=1 AND is_real_name=1` 排除已是 matchmaker 的用户

### 3.9 删除约见申请 `DELETE /admin/matchmaker/meetings/requests/{request_id}` ✅ M4 新增

**业务规则**：
- 记录不存在 → **404**
- 该申请已被安排约会（`meeting_record.request_id = {id}`）→ **409**「该申请已生成约会记录，请先删除对应约会」

### 3.10 删除约会记录 `DELETE /admin/matchmaker/meetings/{meeting_id}` ✅ M4 新增

**业务规则**：
- 记录不存在 → **404**
- `status='COMPLETED'` → **409**「该约会已完成，不可删除」
- 写入 `business_audit_log` (`action='meeting.delete'`)

### 3.11 后端代码位置

- 路由：`app/api/routes/meeting.py` `admin_router` 段（13 端点）。**声明顺序**：`POST /requests/{id}/schedule` → `GET /requests` → `PATCH /requests/{id}` → `GET ""` → `POST ""` → `GET /statistics` → `GET /options` → `GET /{meeting_id}` → `PATCH /{meeting_id}` → `GET /{meeting_id}/feedback` → `DELETE /requests/{id}` → `DELETE /{meeting_id}`（**静态路径必须排在 `/{meeting_id}` 之前**，否则 `/options`、`/statistics` 会被动态段吞掉）
- Schema：`app/schemas/meeting.py`（`MeetingRequestAdminPage`/`MeetingRecordAdminPage`/`MeetingStatusUpdate`/`MeetingScheduleCreate`/`MeetingDirectCreate`/`MeetingStatistics`/`MeetingRecordAdminUpdate`/`MeetingFeedbackAdminItem`）
- Service：`app/services/meeting.py` 内 `admin_list_requests`/`admin_update_request`/`admin_list_meetings`/`admin_get_meeting`/`admin_create_meeting`/`admin_meeting_statistics`/`admin_update_meeting`/`admin_feedback`/`schedule_meeting`/`admin_options`/`admin_delete_request`/`admin_delete_meeting`；约会记录统一走 `_ADMIN_RECORD_SELECT`（JOIN 出双方昵称/红娘名/反馈条数）

### 3.12 约会统计 `GET /admin/matchmaker/meetings/statistics` ✅ M4 收尾新增

无参数。返回 `MeetingStatistics`：

| 字段 | 口径 |
|---|---|
| `total_arranged` | `meeting_record` 全表计数 |
| `total_met` | `status IN ('CHECKED_IN','COMPLETED')` 计数 |
| `month_arranged` | `scheduled_at` 落在当前自然月 |
| `month_waiting` | 当前月 且 `status IN ('SCHEDULED','REMINDED')` |
| `month_met` | 当前月 且 `status IN ('CHECKED_IN','COMPLETED')` |
| `month_not_met` | 当前月 且 `status IN ('NO_SHOW','CANCELLED')` |

> 「当前月」由 MySQL `UTC_TIMESTAMP()` 判定。

### 3.13 添加约会 `POST /admin/matchmaker/meetings` ✅ M4 收尾新增

约会管理页「添加约会」用：直接指定双方与服务红娘，**无需先存在约见申请**（自动补建一条 `meeting_request(status='ACCEPTED')`）。

**body** `MeetingDirectCreate`：

| 字段 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|
| `from_user_id` | int | ✅ | — | ≥1 | 男方会员ID（写入 `meeting_request.user_id`） |
| `to_user_id` | int | ✅ | — | ≥1 | 女方会员ID（写入 `target_user_id`） |
| `organizer_id` | int | ✅ | — | ≥1 | 本次约见服务红娘 |
| `organization_id` | int \| null | — | — | ≥1 | 门店 |
| `scheduled_at` | datetime \| null | — | `NOW()` | — | 见面时间（留空即「待确定」） |
| `location` | string \| null | — | `待确定` | ≤255 | 见面地点 |
| `member_visible` | bool | — | `true` | — | 会员端可见 |
| `sms_remind` | bool | — | `true` | — | 短信提醒 |
| `met` | bool | — | `false` | — | 是否已见面；`true` → 记录直接落 `COMPLETED`（计入服务「成功」次数） |

**返回**：201 `MeetingRecordResponse`。

**错误**：双方相同 → 422「约会双方不能为同一会员」；男方/女方不存在或 `status<>1` → 404「男方或女方会员不存在」；红娘不存在 → 404「服务红娘不存在」。

**副作用**：写 `business_audit_log(action='meeting.create', resource_type='meeting_record')`。

---

## 四、会员服务·红娘牵线（2 端点）

入口：`/api/v1/admin/matchmaker/match-records`（权限：读 `matchmaker.service.read`、写 `matchmaker.service.manage`）

### 4.1 列表 `GET /api/v1/admin/matchmaker/match-records`

**query**：`page`(1-1000) / `page_size`(1-100) / `search`(≤64，男方或女方昵称/手机 LIKE)。

**返回** `MatchRecordPage`，item 字段：`id` `from_user_id` `to_user_id` `status` `created_at` `responded_at` `from_nickname` `to_nickname` `matchmaker_id`（数据源 `match_apply` + `users`×2，红娘由 `resource_assignment` 派生）。

### 4.2 新建 `POST /api/v1/admin/matchmaker/match-records`

**body** `{from_love_user_id:int, to_love_user_id:int, create_time:datetime, complete_time:datetime, line_status: 1|2}`（1=成功 2=失败）。

**业务规则**：
- 双方相同 → 422「牵线会员与被牵线会员不能相同」
- `complete_time < create_time` → 422「牵线完成时间不能早于申请时间」
- 双方会员必须存在 → 否则 404
- 同 `(from,to,create_time)` 已存在 → 409「该牵线记录已存在」
- `line_status=1` 时同步写 `user_match` 双向关系（`ON DUPLICATE KEY UPDATE status=1`）

---

## 五、会员服务·服务申请（2 端点）

### 5.1 列表 `GET /api/v1/admin/matchmaker/service-requests`

**query**：`page/page_size/status/service_type/user_id`。

**返回** `ServiceRequestPage`，item 字段：`id `user_id `user_name `service_type` `status `applied_at` `handled_at` `remark`。

### 5.2 处理 `PATCH /admin/matchmaker/service-requests/{request_id}`

**body** `{status: 'APPROVED'|'REJECTED', remark?: string}`。

**业务规则**：approved 后自动扣减 `matchmaker_service_quota` 对应配额；rejected 不扣。

---

## 六、会员服务·推广管理（5 端点）✅ M4 收尾新增

入口：`/api/v1/admin/promotion-orders`（权限：读 `matchmaker.service.read`、写 `matchmaker.service.manage`；由 `app/api/dependencies.py::_matchmaker_admin_permission` 按 `/promotion-orders` 子串自动映射）
数据源：`promotion_order`（+ `users` 取购买人昵称）

### 6.1 列表 `GET /api/v1/admin/promotion-orders`

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | 1-1000 | — |
| `page_size` | int | 20 | 1-100 | — |
| `pay_status` | string \| null | — | `^(unpaid\|paid\|refunded)$` | 支付状态 |
| `status` | string \| null | — | `^(pending\|processing\|done\|cancelled)$` | 订单状态 |
| `search` | string \| null | — | ≤64 | 订单号/昵称/套餐名 LIKE，或 ID 精确 |

**返回** `PromotionOrderPage {items,page,page_size,total,has_more}`，item 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 主键 |
| `order_no` | string | 支付订单号（唯一） |
| `user_id` / `user_nickname` | int / string \| null | 购买推广人 |
| `product_name` | string | 推广套餐名 |
| `amount` | **string** | 订单金额，固定 2 位小数字符串（Decimal 序列化，前端禁止 Number 运算） |
| `pay_status` | string | `unpaid` 未支付 / `paid` 已支付 / `refunded` 已退款 |
| `pay_method` | string \| null | `wechat` / `alipay` / `balance` / `offline` |
| `status` | string | `pending` 待处理 / `processing` 推广中 / `done` 已完成 / `cancelled` 已取消 |
| `remark` | string \| null | 备注 |
| `paid_at` | datetime \| null | 支付时间（置为 paid 时自动补 `UTC_TIMESTAMP()`，其余状态自动清空） |
| `created_at` / `updated_at` | datetime | — |

**空数据示例**：`{"items":[],"page":1,"page_size":20,"total":0,"has_more":false}`

### 6.2 统计 `GET /api/v1/admin/promotion-orders/statistics`

无参数。返回 `PromotionOrderStatistics {total, paid_count, paid_amount(string), processing_count}`。

### 6.3 详情 `GET /api/v1/admin/promotion-orders/{order_id}`

**path**：`order_id`(≥1)。返回 `PromotionOrder`；不存在 → 404「推广订单不存在」。

### 6.4 更新 `PATCH /api/v1/admin/promotion-orders/{order_id}`

**body** `PromotionOrderUpdate`：`pay_status?` / `pay_method?(≤32)` / `status?` / `remark?(≤500)`，**至少一项**（否则 422）。

**业务规则**：
- `pay_status=paid` → `paid_at = COALESCE(paid_at, UTC_TIMESTAMP())`
- `pay_status` 改为 `unpaid`/`refunded` → `paid_at` 置 `NULL`
- 写 `business_audit_log(action='promotion_order.update', resource_type='promotion_order')`

**非法示例**：`{}` → 422「至少提供一个需要修改的字段」

### 6.5 删除 `DELETE /api/v1/admin/promotion-orders/{order_id}`

**返回** `{id, deleted: true}`；写审计 `action='promotion_order.delete'`。

### 6.6 后端代码位置

- 路由：`app/api/routes/promotion_order_admin.py`（`/statistics` 声明在 `/{order_id}` 之前）
- Schema：`app/schemas/promotion_order_admin.py`
- Service：`app/services/promotion_order_admin.py`（`list_orders`/`get_order`/`update_order`/`delete_order`/`order_statistics`；`_money()` 统一 Decimal→2 位小数字符串）

---

## 七、错误码表

| HTTP | 触发条件 | 前端处理建议 |
|---|---|---|
| 401 | 未登录 / token 过期 / 签名错误 | 跳转 `/login`，清 token |
| 403 | 权限点缺失 | toast「无权限」并隐藏入口 |
| 404 | 记录不存在 | toast「数据不存在，请刷新」 |
| 409 | 业务状态不允许（如已分配/已完成的约会/状态机越级/账号未绑定红娘） | toast 服务端 `detail` 文案 |
| 410 | 已过恢复窗口 | toast「已超过恢复期，无法恢复」 |
| 422 | 参数校验失败（必填/正则/范围） | 显示字段级错误 |
| 500 | 未捕获异常 | 提示联系管理员 |

---

## 八、文档完成自检清单

- [x] 每个端点的请求参数表（参数名/位置/类型/必填/默认值/校验/业务含义）
- [x] 至少 1 个非法示例（phone/wechat 双空、`.xls` 上传、`PromotionOrderUpdate` 空 body、状态机越级）
- [x] 每个返回体的字段表（含嵌套展开与业务含义；金额统一 string）
- [x] 空数据示例（`items: []`）
- [x] 业务规则（状态机、配额扣减、联系方式唯一性、模板列匹配、标签自动补建）
- [x] 错误码表（HTTP + 触发条件 + 前端处理）
- [x] 与代码（`app/api/routes/*.py` `app/services/*.py` `app/schemas/*.py`）一致

## 九、前端对应页面

| 页面 | 路由 | 调用的端点 |
|---|---|---|
| 线索管理 | `/love-customer-list` | 2.1/2.2/2.6/2.7/2.8/2.10/2.11/2.12/2.13 |
| 数据报表 | `/love-customer-statistics` | 2.1/2.3 |
| 跟进全览 | `/customer-follow-up` | 跟进全览（见 M3 文档） |
| 客源批量导入 | `/love-customer-batch-import` | 2.14/2.15/2.16 |
| 弃海客源 | `/love-customer-abandon` | 2.4/2.6/2.13 |
| 弃海记录 | `/love-customer-abandon-log` | 2.5/2.6 |
| 功能配置 | `/love-customer-config` | `GET/PATCH /admin/configs/tools_customer_leads` |
| 红娘牵线记录 | `/vip-line-record` | 4.1/4.2 |
| 约见申请 | `/love-interview` | 3.1/3.2/3.3/3.6/3.8/3.9 |
| 约会管理 | `/love-appointment` | 3.4/3.5/3.6/3.8/3.12/3.13 |
| 推广管理 | `/love-promotion` | 6.1/6.3/6.4/6.5 |
| 推广分成明细（跳转壳） | `/vip-popularize-record` | → `/poplove-matchmaker-distribution-details` |

## 十、变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-09-11 | v1 | 新建：客源线索 13 + 约见/约会 7 + 红娘牵线 1 + 服务申请 2 = 共 23 端点 | M4 后端首版契约 |
| 2026-09-12 | v1.1 | 约见/约会新增 3 端点（GET /options / DELETE /requests/{id} / DELETE /{id}） | 补齐管理后台 CRUD 能力 |
| 2026-09-12 | **v1.2** | **M4 收尾**：客服线索 +3（options / import-template / import）、约见约会 +2（statistics / POST 直接建约会）、新增推广管理 5 端点；`customer_lead` 补 `promoter_id`/`audit_status`，`meeting_record` 补 `member_visible`/`sms_remind`，新表 `promotion_order`；约见申请加 `status_group`、约会列表加 `search`/`organizer_id`/`met`/日期范围；修正 `/meetings/options` 被 `/{meeting_id}` 吞掉的路由顺序 bug | 全量 **28 → 38 端点**；M4 前端 12 页全部接线 |