# 活动报名·互选 接口契约（M7-A）

> **模块范围**：管理后台「活动」+「互选」菜单下 5 个页面
>
> - 活动参数配置 `/active-config`
> - 活动列表（创建/复制/状态/链接）`/active-list`
> - 活动报名管理（审核/导入/签到/CRM 入库）`/active-signupmanager`
> - 互选活动列表 `/mutual-selection-list`
> - 互选记录 `/mutual-selection-record`
>
> 活动运营方案 `/active-alliance`：纯静态运营说明页，不接后端。
>
> **后端文件**：
> 1. 活动 / 报名：`app/api/routes/activity_admin.py`
> 2. 互选活动 / 参与嘉宾：`app/api/routes/mutual_selection_admin.py`
> 3. 配置域（`tools_active` / `tools_active_alliance`）：走通用接口 `/api/v1/admin/configs`

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：红娘后台独立 Token
- 请求头：`Authorization: Bearer <access-token>`
- 所有 admin 端点未登录 → 401；权限点缺失 → 403

### 1.2 权限点

| 路径前缀 | 读取 | 写入 |
|---|---|---|
| `/admin/activities/*` | `community.activity.read` | `community.activity.manage` |
| `/admin/activity-signups/*` | `community.activity.read` | `community.activity.manage` |
| `/admin/mutual-activities/*` | `community.activity.read` | `community.activity.manage` |
| `/admin/mutual-records/*` | `community.activity.read` | `community.activity.manage` |
| 配置域 `tools_active` / `tools_active_alliance` | `platform.config.read` | `platform.config.write` |

`/mutual-activities` 与 `/mutual-records` 均命中 `/mutual-` 关键词映射，与 `/activities`、`/activity-signups` 同口径（`community.activity.read/manage`），无权限冲突。

### 1.3 响应与异常

- 统一响应壳 `{ items | data, page, page_size, total, has_more }`，单条返回裸对象。
- 统一异常码：401 未登录 / 403 无权限 / 404 不存在 / 409 冲突 / 422 参数错误 / 500 服务器错误。
- `id` 路由前需先声明静态段（`/options`、`/signups`、`/link`、`/copy`、`/status`）。

---

## 二、活动管理（`/api/v1/admin/activities`）

### 2.1 列表 `GET /admin/activities`

查询参数：

| 字段 | 类型 | 说明 |
|---|---|---|
| `keyword` | string | 模糊匹配 title / organizer |
| `type` | string | 活动类型（自由文本分类） |
| `audit_status` | enum | `pending` / `approved` / `rejected` |
| `online` | int | 1=上线 / 0=下线 |
| `status` | int | 1=草稿 / 2=报名中 / 3=进行中 / 4=已结束 / 5=已取消 |
| `page` / `page_size` | int | 默认 1 / 20 |

响应：`ActivityAdminPage`，字段同步字段：

```json
{
  "id": 1, "title": "夏季相亲游园会", "cover": "...", "cover_small": "...",
  "type": "户外活动", "city": "杭州", "address": "西湖边公园",
  "start_time": "2026-06-01T14:00:00", "end_time": "2026-06-01T17:00:00",
  "signup_deadline": "2026-05-30T18:00:00",
  "time_text": "6 月 1 日 14:00-17:00",
  "organizer": "杭州红娘分会",
  "fee_name": "报名费", "price": 99.0, "price_male": 99.0, "price_female": 79.0,
  "signup_mode": "member", "require_realname": true, "limit_mode": "gender",
  "max_people": 40, "max_male": 20, "max_female": 20,
  "virtual_people": 30, "virtual_female": 15,
  "hide_signup_count": false,
  "reward_promoter": 10.0, "reward_service": 20.0, "reward_partner": 15.0,
  "reminder_html": "<p>...</p>",
  "service_wechat": "matchmaker01", "service_qr": "https://...",
  "virtual_views": 1000, "sort_order": 100, "custom_share": true,
  "manager_ids": "101,102", "notify_phones": "138...,139...",
  "online": true, "audit_status": "approved", "status": 2,
  "current_people": 12, "male_count": 6, "female_count": 6,
  "link_url": "https://.../activity/1", "qr_code": "https://...qr.png",
  "created_by": 1, "created_at": "2026-04-01T10:00:00"
}
```

### 2.2 创建 `POST /admin/activities`

请求体同 `ActivityAdminItem` 去掉 `id/created_by/created_at/current_people/male_count/female_count/link_url/qr_code`。

### 2.3 详情 `GET /admin/activities/{activity_id}`
返回 `ActivityAdminItem`。

### 2.4 修改活动 `PATCH /admin/activities/{activity_id}`
请求体同 Create，所有字段 optional，至少一个字段。

### 2.5 修改活动状态 `PATCH /admin/activities/{activity_id}/status`

```json
{ "status": 2, "reason": "已截止报名" }
```

### 2.6 复制活动 `POST /admin/activities/{activity_id}/copy`
返回新活动（标题加「(副本)」，覆盖所有业务字段，复用当前用户作为 created_by）。

### 2.7 链接与二维码 `GET /admin/activities/{activity_id}/link`

```json
{ "link_url": "https://.../activity/1", "qr_code": "https://...qr.png" }
```

### 2.8 删除活动 `DELETE /admin/activities/{activity_id}` → 204
级联软删相关报名与互选记录（不动线下记录主键）。

**变更记录（2026-10-04）**：新增前置拦截——存在「已支付」或「已签到」的报名时返回 **409**，需先处理这些报名（退款/取消）再删除活动，避免已收款凭证随活动一起消失。

### 2.9 字典下拉 `GET /admin/activities/options`

```json
[{ "id": 1, "title": "夏季相亲游园会" }]
```

---

## 三、活动报名管理（`/api/v1/admin/activity-signups`）

### 3.1 列表 `GET /admin/activity-signups`

参数：`activity_id`、`keyword`（昵称/手机/真名）、`search_by`（`nickname` / `phone`）、`status`（0 待审 / 1 已审核 / 2 取消 / 3 未通过）、`first_signup`、`gender`、`pay_status`（`free` / `paid` / `unpaid`）、`checked_in`、`signup_from` / `signup_to`（`YYYY-MM-DD`）、`page` / `page_size`。

**变更记录（2026-09-28）**：新增跨活动查询和服务端筛选参数，旧的 `activity_id` 查询方式保持兼容；前端不再只拉取前 100 条后本地筛选。

响应：`ActivitySignupAdminPage`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | |
| `activity_id`, `activity_title` | int/string | |
| `user_id`, `nickname`, `avatar` | | 会员资料快照 |
| `real_name`, `phone`, `id_card`, `is_realname` | | 实名校验 |
| `gender`, `age`, `height`, `education`, `income`, `marriage_status`, `company`, `is_member` | | 资料快照 |
| `remark` | string | |
| `status` | enum(0,1,2,3) | |
| `pay_status` | enum(free/paid/unpaid) | |
| `pay_amount` | float | |
| `checked_in` | bool | 是否签到 |
| `in_crm` | bool | 是否已沉淀 CRM 客源 |
| `promoter_id`, `promoter_name` | | 推广红娘归属 |
| `signup_times` | int | 该用户历史报名次数 |
| `created_at`, `updated_at` | | |

### 3.2 统计卡 `GET /admin/activity-signups/statistics`

参数：`activity_id` 可选；返回：

```json
{
  "total": 36, "first_signup": 12, "pending": 4, "approved": 28,
  "rejected": 4, "fee_amount": "2680.00",
  "not_checked_in": 20, "checked_in": 16, "in_crm": 22
}
```

### 3.3 字典下拉 `GET /admin/activity-signups/options`
同 2.9。

### 3.4 导出 `GET /admin/activity-signups/export`
查询参数与列表一致；返回 `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，文件名 `activity-signups-YYYYMMDD.xlsx`。

### 3.5 报名详情 `GET /admin/activity-signups/{signup_id}`

### 3.6 修改/审核报名 `PATCH /admin/activity-signups/{signup_id}`
请求体：`ActivitySignupUpdate`（任选字段，`status/pay_status/checked_in/in_crm/promoter_id` 自由组合）。

### 3.7 调整报名状态 `PATCH /admin/activity-signups/{signup_id}/status`
专用于轻量级审核：

```json
{ "status": 1, "reason": "审核通过" }
```

### 3.8 删除报名 `DELETE /admin/activity-signups/{signup_id}` → 204

**变更记录（2026-10-04）**：原实现为**物理删除且不写审计**，与本节契约不符；现已按契约落地为软删除。

- 查询参数：`reason`（可选，≤255 字），写入审计。
- 行为：`UPDATE activity_signup SET deleted_at = UTC_TIMESTAMP(), deleted_by = <后台账号 id>`，并向 `business_audit_log` 写入 `before_json` 快照（含被删前完整行）。
- **拦截（409）**：报名已支付（`pay_status = 'paid'`）或已签到（`checked_in = 1`）时不允许删除，必须先走退款/取消流程，防止「删掉即抹除已收款凭证」。
- 报名已删除（`deleted_at` 非空）或不存在 → 404。
- 所有报名读取路径（活动详情/列表的男女计数、报名列表、统计卡、导出、报名详情、`PATCH` 修改）均只返回 `deleted_at IS NULL` 的记录，因此删除后不会出现在任何报表口径中。
- 字段：`deleted_at`（datetime，非空表示已删除）、`deleted_by`（bigint，后台账号 id）。迁移脚本：`migrations/m4_m7/20261004_01_activity_signup_soft_delete_{up,down,verify}.sql`。

---

## 四、互选活动（`/api/v1/admin/mutual-activities`）

### 4.1 列表 `GET /admin/mutual-activities`

参数：`title` / `status`（1报名中 / 2进行中 / 3已结束） / `page` / `page_size`。

### 4.2 创建 `POST /admin/mutual-activities`

| 字段 | 类型 | 必填 | 校验 | 说明 |
|---|---|---|---|---|
| `title` | string | 是 | 1–128 | 活动标题 |
| `cover` | string | 否 | ≤500 | 封面图 |
| `city` | string | 否 | ≤64 | 城市 |
| `start_time`, `end_time` | datetime | 是 | `end_time > start_time` | 活动起止 |
| `signup_deadline` | datetime | 否 | — | 报名截止（兼容旧字段） |
| `max_male`, `max_female` | int | 否 | ≥0 | 男女名额 |
| `description` | string | 否 | — | 活动说明 |
| `status` | int | 否 | 1/2/3 | 1 报名中 2 进行中 3 已结束 |
| `signup_start_time` | datetime | 否 | 与 `signup_end_time` 成对时 `end > start` | **B2** 报名开始 |
| `signup_end_time` | datetime | 否 | 同上 | **B2** 报名结束 |
| `mutual_start_time` | datetime | 否 | 与 `mutual_end_time` 成对时 `end > start` | **B2** 互选开始 |
| `mutual_end_time` | datetime | 否 | 同上 | **B2** 互选结束 |
| `activity_fee` | decimal | 否 | ≥0；`null` = 不收费 | **B2** 活动费用 |
| `show_wechat_direct` | bool | 否 | 默认 `true` | **B2** 是否展示微信直联入口 |
| `show_matchmaker_push` | bool | 否 | 默认 `false` | **B2** 是否展示红娘推送入口 |

**B2 请求示例**：

```json
{
  "title": "双十一互选", "start_time": "2026-11-11T09:00:00", "end_time": "2026-11-13T18:00:00",
  "signup_start_time": "2026-11-01T09:00:00", "signup_end_time": "2026-11-10T18:00:00",
  "mutual_start_time": "2026-11-11T09:00:00", "mutual_end_time": "2026-11-13T18:00:00",
  "activity_fee": 99, "show_wechat_direct": false, "show_matchmaker_push": true
}
```

**非法示例（报名结束早于开始，422）**：

```json
{ "title": "x", "start_time": "2026-11-11T09:00:00", "end_time": "2026-11-13T18:00:00",
  "signup_start_time": "2026-11-10T09:00:00", "signup_end_time": "2026-11-09T18:00:00" }
```

**时间窗校验**：
- `MutualActivityCreate` 在 schema 层校验 `end_time > start_time`、报名与互选各自的起止顺序；
- `MutualActivityUpdate` 只传半边时无法在 schema 交叉校验，由服务层按「合并后的行值」
  校验并返回 **422**（`end_time 必须晚于 start_time` / `signup_end_time 必须晚于 signup_start_time` /
  `mutual_end_time 必须晚于 mutual_start_time`）。

### 4.3 详情 `GET /admin/mutual-activities/{activity_id}`

返回 `MutualActivityItem`，在 4.2 字段基础上含 `id`、`visible`、`participant_count`、
`copy_count`、`created_at`、`updated_at`，以及 B2 的 7 个新字段。

### 4.4 修改 `PATCH /admin/mutual-activities/{activity_id}`

可改 4.2 全部字段（部分更新）。时间窗按合并后值校验，见上。

**复制**（4.6）会一并复制 B2 的 7 个字段（`copy_activity` 的硬编码列清单已同步扩展）。

### 4.5 上线/下线 `PATCH /admin/mutual-activities/{activity_id}/visible`
`{ "online": bool }`。

### 4.5b 下线（新增）`POST /admin/mutual-activities/{activity_id}/offline`

| 参数 | 位置 | 类型 | 必填 | 校验 | 说明 |
|---|---|---|---|---|---|
| `activity_id` | path | int | 是 | ≥1 | 活动 ID |
| `reason` | body | string | 否 | ≤255 | 下线原因（写入审计 `reason`） |

```json
{ "reason": "报名人数不足，本场取消" }
```

成功 `200` 返回 `MutualActivityItem`（`visible=false`），并写审计
`mutual_activity.offline`。`PATCH /visible` 保持兼容，新前端建议用本接口（带原因可追溯）。

### 4.6 复制 `POST /admin/mutual-activities/{activity_id}/copy`

### 4.7 删除 `DELETE /admin/mutual-activities/{activity_id}` → 204

### 4.8 参与嘉宾列表 `GET /admin/mutual-activities/{activity_id}/participants`

```json
[{
  "id": 1, "user_id": 100, "nickname": "小芳", "avatar": "...",
  "gender": "female", "age": 26, "city": "杭州",
  "pick_count": 0, "selected_count": 0, "joined_at": "2026-04-01T..."
}]
```

### 4.9 添加参与嘉宾 `POST /admin/mutual-activities/{activity_id}/participants`
请求体：`{ user_id: int }`。

### 4.10 移除参与嘉宾 `DELETE /admin/mutual-activities/{activity_id}/participants/{user_id}` → 204

---

## 五、互选记录（`/api/v1/admin/mutual-records`）

独立表 `mutual_selection_pick` 存储每条 `pick` 记录（`from_user_id` 选 `to_user_id`）。

> D-7 修正：本节早期写为 `/api/v1/admin/matchmaker/mutual-picks`，与代码不符。
> 实际路由前缀为 `record_router = APIRouter(prefix="/admin/mutual-records")`
> （`app/api/routes/mutual_selection_admin.py:21`）。以下以代码为准。

### 5.1 列表 `GET /api/v1/admin/mutual-records`

参数：`activity_id` / `actor_id` / `keyword` / `result`（`success|fail|none`） / `page` / `page_size`。

**会员资料筛选（2026-10-27 新增，均作用于发起方 `from_user`）**：

| 参数 | 类型 | 校验 | 语义 | 落地列 |
|---|---|---|---|---|
| `age_min` / `age_max` | int | 0-120 | 发起方年龄区间（含），按生日换算 | `users.birthday`（`TIMESTAMPDIFF(YEAR, ...)`） |
| `income_min` / `income_max` | float | ≥0 | 发起方收入区间（元，含），前端把「小于10w / 10-20w / 30-50w / 50-100w / 100w以上」档位换算成上下限传入 | `user_profile.income` |
| `income_secret` | bool | — | `true` 仅看收入「保密」（未填写）的发起方 | `user_profile.income IS NULL` |
| `education` | string | ≤255，英文逗号分隔多选 | 发起方学历多选 | `user_auth.education` |

说明：与会员 CRM 列表同口径（年龄取生日、收入取 `user_profile.income` 数值区间、学历取 `user_auth.education`）；资料列以 `EXISTS` 子查询限定到 `from_user_id`，不影响被选方、不放大 `total`。不传资料参数时行为与旧版完全一致。

| 字段 | 说明 |
|---|---|
| `id` | |
| `activity_id`, `activity_title` | |
| `from_user_id`, `from_nickname` | 主动选择人 |
| `to_user_id`, `to_nickname` | 被选中人 |
| `is_mutual` | 是否互选成功 |
| `created_at` | |

### 5.2 筛选下拉字典 `GET /api/v1/admin/mutual-records/options`

### 5.3 删除 / 标记超级喜欢 —— 未实现

> 早期文档声明的 `DELETE /api/v1/admin/matchmaker/mutual-picks/{pick_id}` 与
> `POST .../mutual-picks/{pick_id}/super` 在代码中**不存在对应路由**（`mutual_selection_admin.py`
> 的 `record_router` 仅注册 `GET ""` 与 `GET /options` 两条）。如需运营侧删除/置顶，请另立需求。

> 注：C 端**没有** `/api/v1/matchmaker/mutual-picks` 路由（全仓无 `mutual-picks` 命中）；
> 运营侧互选记录一律走本节 `/admin/mutual-records`。

---

## 六、配置域（平台配置通用接口）

### 6.1 活动参数配置 `tools_active`

域定义：`app/services/admin_config.py:DEFAULT_CONFIGS["tools_active"]`

```json
{
  "categories": [
    { "name": "户外活动", "sort": 100, "status": 1, "icon": "https://..." }
  ],
  "signin_code_mode": "auto",
  "auto_approve": false,
  "signin_remind_hours": 12,
  "show_virtual_people": true,
  "banner_url": "https://..."
}
```

调用：`GET /api/v1/admin/configs/tools_active` / `PUT /api/v1/admin/configs/tools_active`

### 6.2 活动运营方案 `tools_active_alliance`

```json
{ "content_html": "<p>...</p>", "enabled": true, "updated_at": "..." }
```

> 此域仅供 `/active-alliance` 静态页读取，运营方案说明文字本身不显示在后台。

---

## 七、数据库变更

### 7.1 表新增

#### `merchant_category`
见 `m7-merchant-and-short-video.md`（同期同库）。

#### `mutual_selection_activity`
```sql
CREATE TABLE `mutual_selection_activity` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `title` varchar(128) NOT NULL,
  `cover` varchar(255) DEFAULT NULL,
  `city` varchar(64) DEFAULT NULL,
  `start_time` datetime NOT NULL,
  `end_time` datetime NOT NULL,
  `signup_deadline` datetime DEFAULT NULL,
  `max_male` int unsigned NOT NULL DEFAULT 0,
  `max_female` int unsigned NOT NULL DEFAULT 0,
  `description` text,
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1报名中/2进行中/3已结束',
  `online` tinyint NOT NULL DEFAULT 1,
  `created_by` int unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_msa_city` (`city`, `start_time`),
  KEY `idx_msa_status` (`status`, `online`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选活动'
```

#### `mutual_selection_participant`
```sql
CREATE TABLE `mutual_selection_participant` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `activity_id` bigint unsigned NOT NULL,
  `user_id` bigint unsigned NOT NULL,
  `joined_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `remark` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_msp_activity_user` (`activity_id`, `user_id`),
  KEY `idx_msp_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选参与嘉宾'
```

#### `mutual_selection_pick`
```sql
CREATE TABLE `mutual_selection_pick` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `activity_id` bigint unsigned NOT NULL,
  `from_user_id` bigint unsigned NOT NULL,
  `to_user_id` bigint unsigned NOT NULL,
  `is_mutual` tinyint NOT NULL DEFAULT 0 COMMENT '0未达成/1达成',
  `note` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mspick_dup` (`activity_id`, `from_user_id`, `to_user_id`),
  KEY `idx_mspick_to` (`to_user_id`, `activity_id`),
  KEY `idx_mspick_mutual` (`activity_id`, `is_mutual`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='互选 pick 记录'
```

### 7.2 表补列

#### `offline_activity`
补：`time_text`、`cover_small`、`organizer`、`fee_name`、`price_male`、`price_female`、`signup_mode`、`require_realname`、`limit_mode`、`max_male`、`max_female`、`virtual_people`、`virtual_female`、`hide_signup_count`、`reward_promoter`、`reward_service`、`reward_partner`、`reminder_html`、`service_wechat`、`service_qr`、`virtual_views`、`sort_order`、`custom_share`、`manager_ids`、`notify_phones`、`online`、`audit_status`、`link_url`、`qr_code`、`created_by_in_session`。

#### `activity_signup`
补：`gender`、`age`、`height`、`education`、`income`、`marriage_status`、`company`、`avatar_url`、`id_card`、`is_realname`、`signup_times`、`pay_status`、`pay_amount`、`checked_in`、`in_crm`、`promoter_id`、`remark_history`。

幂等迁移函数：`_ensure_m7_columns`（`database_setup_marriage.py`）。

---

## 八、错误码表

| 状态码 | 场景 |
|---|---|
| 400 `ACTIVITY_TIME_INVALID` | 开始 ≥ 结束 |
| 400 `ACTIVITY_DEADLINE_INVALID` | 报名截止晚于开始时间 |
| 404 `ACTIVITY_NOT_FOUND` / `SIGNUP_NOT_FOUND` / `MUTUAL_ACTIVITY_NOT_FOUND` |
| 409 `MUTUAL_PARTICIPANT_DUPLICATE` | 嘉宾已添加 |
| 409 `MUTUAL_PICK_DUPLICATE` | 同一用户对同一嘉宾只能 pick 一次 |
| 422 `ACTIVITY_DELETE_HAS_PAYED` | 已支付报名存在，禁止直接删除活动 |

---

## 九、前端对应页面

| 页面 | 路径 |
|---|---|
| 活动参数配置 | `src/app/(admin)/active-config/page.tsx`（useConfigDomain('tools_active')） |
| 活动列表 | `src/app/(admin)/active-list/page.tsx` |
| 活动报名管理 | `src/app/(admin)/active-signupmanager/page.tsx` |
| 互选活动列表 | `src/app/(admin)/mutual-selection-list/page.tsx` |
| 互选记录 | `src/app/(admin)/mutual-selection-record/page.tsx` |
| 活动运营方案 | `src/app/(admin)/active-alliance/page.tsx`（纯静态说明，无接口） |

---

## 十、文档完成自检清单

- [x] Schema/Service/Route 三件套落地
- [x] 14 + 10 = 24 个端点 OpenAPI 已注册
- [x] 测试 8 用例覆盖 route 顺序/状态/字段/迁移
- [x] 前端 `tsc --noEmit` 通过
- [x] `_ensure_m7_columns` 注册进 `_ensure_matchmaker_staff_defaults` 主流程
- [x] 文档与代码同步

---

## 十一、变更记录

- **2026-09-12 v1.0** —— M7-A 首版（含活动报名 + 互选 + 配置域）
- **2026-10-20 v1.1** —— B2：互选活动补 `signup_start_time` / `signup_end_time` /
  `mutual_start_time` / `mutual_end_time` / `activity_fee` / `show_wechat_direct` /
  `show_matchmaker_push` 七个字段，新增 `POST /admin/mutual-activities/{id}/offline`。
  新增字段均为可选，旧客户端不受影响；迁移见
  `migrations/m4_m7/20261020_01_backoffice_field_gaps_*.sql`。
