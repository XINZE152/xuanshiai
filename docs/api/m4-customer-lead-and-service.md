# 客源线索 & 会员服务 接口契约（M4）

> 覆盖 4 个后端模块：
> 1. **客源线索**：`app/api/routes/customer_leads_admin.py`（13 端点）
> 2. **会员服务·约见/约会**：`app/api/routes/meeting.py` 内 `admin_router`（11 端点）
> 3. **会员服务·红娘牵线**：`app/api/routes/matchmaker_crm_admin.py` 内 `match_records`（2 端点）
> 4. **会员服务·服务申请**：`app/api/routes/matchmaker.py` 内 `admin_router` 内 `service-requests`（2 端点）

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
| `/admin/customer-leads/*` | `matchmaker.lead.read` | `matchmaker.lead.manage` |
| `/admin/matchmaker/service-requests/*` | `matchmaker.service.read` | `matchmaker.service.write` |
| `/admin/matchmaker/match-records/*` | `matchmaker.match.read` | `matchmaker.match.write` |
| `/admin/matchmaker/meetings/*`（GET） | `meeting.read` | `meeting.write`（非 GET） |
| `/admin/matchmaker/meetings/{id}/feedback` | `meeting.feedback.read` | — |

依赖文件：`app/api/dependencies.py:_matchmaker_admin_permission(request)` 按路径子串映射。

### 1.3 响应与异常

- 成功响应**不包裹** `data` 字段
- 错误统一 `HTTPException(status_code, detail="...")` → 响应体 `{"detail": "..."}`，**无业务错误码字段**
- 分页：`items / page / page_size / total / has_more`
- 金额 Decimal 一律序列化为 **字符串**（如 `"12.30"`）
- 时间字段：MySQL `UTC_TIMESTAMP()` 写入，Python 端读取后按 ISO 8601 返回

### 1.4 数据库

无新表。所有端点复用已有表：

- `customer_lead` / `customer_lead_follow_up` / `customer_lead_abandonment` / `customer_lead_tag` / `customer_lead_tag_relation`
- `meeting_request` / `meeting_record` / `meeting_feedback`
- `match_apply`（匹配申请 = 红娘牵线记录）
- `matchmaker_service_quota`（服务配额）

---

## 二、客源线索（13 端点）

### 2.1 列表 `GET /api/v1/admin/customer-leads`

**query 参数**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | 页码 |
| `page_size` | int | 20 | 1-100 | 每页条数 |
| `search_value` | string \| null | — | ≤100 | 关键字（姓名/手机/编号） |
| `status` | int \| null | — | 0/1/2/3/4 | 0待分配 1已分配 2已到店 3已签约 4已弃海 |
| `matchmaker_id` | int \| null | — | ≥1 | 所属红娘 |
| `from_date` / `to_date` | string \| null | — | `^\d{4}-\d{2}-\d{2}$` | 创建日期范围（左闭右开） |

**返回** `CustomerLeadPage`，item 字段：`id `member_code (G+6位)`name `phone(掩码)`gender` `birthday` `status/status_label` `matchmaker_id` `matchmaker_name` `abandoned_at` `created_at`。

### 2.2 录入 `POST /api/v1/admin/customer-leads`

**body** `CustomerLeadCreate`：必填 `name(1-64) `phone(11位纯数字)` gender(1/2) `birthday(YYYY-MM-DD)`，可选 `matchmaker_id`/`remark`/`tag_ids`。

**返回**：201 `CustomerLead`。

### 2.3 统计 `GET /api/v1/admin/customer-leads/statistics`

无参数。返回 `CustomerLeadStatistics`：8 字段，按 status 计数 + 转化漏斗。

### 2.4 弃海池 `GET /api/v1/admin/customer-leads/abandoned`

无参数。返回 `list[CustomerLeadAbandonment]`，仅 status=4。

### 2.5 弃海记录 `GET /api/v1/admin/customer-leads/abandonments`

无参数。返回 `list[CustomerLeadAbandonment]`，全部弃海流水。

### 2.6 详情 `GET /admin/customer-leads/{lead_id}`

**path**：`lead_id`(≥1)。

**返回**：`CustomerLead`（含完整手机号、备注、跟进记录摘要）。

### 2.7 编辑 `PATCH /admin/customer-leads/{lead_id}`

**body** `CustomerLeadUpdate`：全可选，**禁止**修改 status/matchmaker_id（分配与弃海走专门端点）。

### 2.8 跟进记录 `GET /admin/customer-leads/{lead_id}/follow-ups`

无参数。返回 `list[CustomerLeadFollowUp]`（按时间倒序）。

### 2.9 批量导入 `POST /admin/customer-leads/batch-import`

**body** `CustomerLeadBatchImportRequest`：`rows: list[CustomerLeadBatchImportRow]`，每行包含 `name/phone/gender/birthday/matchmaker_name?/remark?`。**前端解析 Excel 后提交**（项目无 openpyxl 后端解析，按 `love-customer-batch-import/page.tsx` 的客户端 `xlsx` 库）。

**返回**：`CustomerLeadBatchImportResult {created, skipped, failed, errors}`。

**业务规则**：
- 同手机号已存在 → skipped（不报错）
- matchmaker_name 找不到 → 该行失败
- 单批 ≤1000 行

### 2.10 新增跟进 `POST /admin/customer-leads/{lead_id}/follow-ups`

**body** `CustomerLeadFollowUpCreate`：`content(必填,≤1000) `next_follow_at?(datetime) `intent_level?(1-3)`。

**返回**：201 `CustomerLeadFollowUp`。

### 2.11 分配 `PATCH /admin/customer-leads/{lead_id}/assignment`

**body** `{matchmaker_id: int}`。

**业务规则**：线索必须在 status=0（待分配）。已分配/已到店等 → 409。

### 2.12 弃海 `POST /admin/customer-leads/{lead_id}/abandon`

**body** `{reason?: string(≤255) `effective_days?: int(默认 30)}`。

**返回**：201 `CustomerLeadAbandonment`（含 `abandoned_at`、`restore_deadline`）。

### 2.13 恢复 `POST /admin/customer-leads/{lead_id}/restore`

**body** 空。

**业务规则**：必须在 `restore_deadline` 之前，否则 → 410（资源不可恢复）。

---

## 三、会员服务·约见/约会（11 端点）

> **入口路径**：`/api/v1/admin/matchmaker/meetings/...`
> **权限**：读 `meeting.read` / 写 `meeting.write`；反馈详情读 `meeting.feedback.read`

### 3.1 约见申请列表 `GET /admin/matchmaker/meetings/requests`

**query**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | ≥1 | — |
| `page_size` | int | 20 | 1-100 | — |
| `status` | string \| null | — | — | `SUBMITTED` / `CONTACTED` / `ACCEPTED` / `DECLINED` / `CLOSED` |
| `search_value` | string \| null | — | ≤64 | 会员昵称/手机 |
| `matchmaker_id` | int \| null | — | ≥1 | 红娘筛选 |
| `from_date` / `to_date` | string \| null | — | ≤32 | YYYY-MM-DD |

**返回** `MeetingRequestAdminPage`，item 字段：`id `requester_id `requester_name `requester_avatar `target_id `target_name `matchmaker_id `matchmaker_name `status `contacted_at `created_at`。

### 3.2 约见申请详情/处理 `PATCH /admin/matchmaker/meetings/requests/{request_id}`

**body** `MeetingStatusUpdate`：`status`(`SUBMITTED`/`CONTACTED`/`ACCEPTED`/`DECLINED`/`CLOSED`) `reason?`(≤255)。

**业务规则**：状态机：`SUBMITTED → CONTACTED → ACCEPTED/DECLINED → CLOSED`。跨级 → 400。

### 3.3 安排约会 `POST /admin/matchmaker/meetings/requests/{request_id}/schedule`

**前置**：当前后台账号必须绑定红娘（`admin.account.matchmaker_user_id` 非 None），否则 409。

**body** `MeetingScheduleCreate`：`scheduled_at`(ISO datetime)`location` `note?`。

**返回**：201 `MeetingRecordResponse`。

### 3.4 约会记录列表 `GET /admin/matchmaker/meetings`

**query**：`page/page_size/status(6 枚举)`。

**返回** `MeetingRecordAdminPage`，item 字段：`id `request_id `matchmaker_id `requester_id `requester_name `target_id `target_name `scheduled_at `location `status `rating `feedback_count`。

### 3.5 约会详情 `GET /admin/matchmaker/meetings/{meeting_id}`

**path**：`meeting_id`(≥1)。

**返回** `MeetingRecordResponse`（完整字段含评分/反馈摘要）。

### 3.6 约会更新 `PATCH /admin/matchmaker/meetings/{meeting_id}`

**body** `MeetingRecordAdminUpdate`：全可选字段。

### 3.7 约会反馈列表 `GET /admin/matchmaker/meetings/{meeting_id}/feedback`

**权限** `meeting.feedback.read`。

**返回** `list[MeetingFeedbackAdminItem]`，字段：`id `meeting_id `user_id `target_rating(1-5) `matchmaker_rating(1-5) `continue_intent(boolean) `private_feedback `created_at`。

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

- 路由：`app/api/routes/meeting.py` 第 72-135 行 `admin_router` 段
- Schema：`app/schemas/meeting.py`（`MeetingRequestAdminPage`/`MeetingRecordAdminPage`/`MeetingStatusUpdate`/`MeetingScheduleCreate`/`MeetingRecordAdminUpdate`/`MeetingFeedbackAdminItem`）
- Service：`app/services/meeting.py` 内 `admin_list_requests`/`admin_update_request`/`admin_list_meetings`/`admin_get_meeting`/`admin_update_meeting`/`admin_feedback`/`schedule_meeting`/`admin_options`/`admin_delete_request`/`admin_delete_meeting`

---

## 四、会员服务·红娘牵线（2 端点）

### 4.1 列表 `GET /api/v1/admin/matchmaker/match-records`

**query**：`page/page_size/status/intent_level/matchmaker_id/from_date/to_date`。

**返回** `MatchApplyPage`，item 字段：`id `requester_id `requester_name `target_id `target_name `matchmaker_id `matchmaker_name `status(intent_level) `success_at `created_at`。

### 4.2 新建 `POST /admin/matchmaker/meetings/requests/{request_id}/schedule`

> 注意：这是约见的 `schedule` 端点，不是 match-records 的 POST；match-records 由 C 端匹配流程自动产生。**管理后台 match-records 仅提供查看**，未开放人工录入接口。

---

## 五、会员服务·服务申请（2 端点）

### 5.1 列表 `GET /api/v1/admin/matchmaker/service-requests`

**query**：`page/page_size/status/service_type/user_id`。

**返回** `ServiceRequestPage`，item 字段：`id `user_id `user_name `service_type` `status `applied_at` `handled_at` `remark`。

### 5.2 处理 `PATCH /admin/matchmaker/service-requests/{request_id}`

**body** `{status: 'APPROVED'|'REJECTED', remark?: string}`。

**业务规则**：approved 后自动扣减 `matchmaker_service_quota` 对应配额；rejected 不扣。

---

## 六、错误码表

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

## 七、文档完成自检清单

- [x] 每个端点的请求参数表（参数名/位置/类型/必填/默认值/校验/业务含义）
- [x] 至少 1 个非法示例（phone 格式、日期格式、状态机越级）
- [x] 每个返回体的字段表（含嵌套展开与业务含义）
- [x] 空数据示例（`items: []`）
- [x] 业务规则（状态机、配额扣减、恢复窗口、权限前置）
- [x] 错误码表（HTTP + 触发条件 + 前端处理 + JSON 示例）
- [x] 与代码（`app/api/routes/*.py` `app/services/*.py` `app/schemas/*.py`）一致

## 八、变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-09-11 | v1 | 新建：客源线索 13 + 约见/约会 7 + 红娘牵线 1 + 服务申请 2 = 共 23 端点 | M4 后端首版契约 |
| 2026-09-12 | v1.1 | 约见/约会新增 4 端点（GET /options / DELETE /requests/{id} / DELETE /{id}） | 补齐管理后台 CRUD 能力 |