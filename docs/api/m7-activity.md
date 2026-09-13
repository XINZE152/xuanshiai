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
| `/admin/mutual-activities/*` | `community.mutual.read` | `community.mutual.manage` |
| 配置域 `tools_active` / `tools_active_alliance` | `platform.config.read` | `platform.config.write` |

`/mutual-activities` 不含 `/activities` 子串，路径映射互不干扰。

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

### 2.9 字典下拉 `GET /admin/activities/options`

```json
[{ "id": 1, "title": "夏季相亲游园会" }]
```

---

## 三、活动报名管理（`/api/v1/admin/activity-signups`）

### 3.1 列表 `GET /admin/activity-signups`

参数：`activity_id`、`keyword`（昵称/手机/真名）、`status`（0 待付 / 1 已审核 / 2 取消 / 3 退款）、`pay_status`、`checked_in`、`in_crm`、`page` / `page_size`。

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
软删（保留审计）。

---

## 四、互选活动（`/api/v1/admin/mutual-activities`）

### 4.1 列表 `GET /admin/mutual-activities`

参数：`title` / `status`（1报名中 / 2进行中 / 3已结束） / `page` / `page_size`。

### 4.2 创建 `POST /admin/mutual-activities`

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` | string | |
| `cover` | string | |
| `city` | string | |
| `start_time`, `end_time` | datetime | |
| `signup_deadline` | datetime | 可空 |
| `max_male`, `max_female` | int | |
| `description` | string | |
| `status` | int | 1/2/3 |

### 4.3 详情 `GET /admin/mutual-activities/{activity_id}`

### 4.4 修改 `PATCH /admin/mutual-activities/{activity_id}`

### 4.5 上线/下线 `PATCH /admin/mutual-activities/{activity_id}/visible`
`{ "online": bool }`。

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

## 五、互选记录

独立表 `mutual_selection_pick` 存储每条 `pick` 记录（`from_user_id` 选 `to_user_id`）。

### 5.1 列表 `GET /api/v1/admin/matchmaker/mutual-picks`

参数：`activity_id` / `from_user_id` / `to_user_id` / `mutual_only`（true 只看互选成功） / `page` / `page_size`。

| 字段 | 说明 |
|---|---|
| `id` | |
| `activity_id`, `activity_title` | |
| `from_user_id`, `from_nickname` | 主动选择人 |
| `to_user_id`, `to_nickname` | 被选中人 |
| `is_mutual` | 是否互选成功 |
| `created_at` | |

### 5.2 删除 `DELETE /api/v1/admin/matchmaker/mutual-picks/{pick_id}` → 204

### 5.3 标记超级喜欢 `POST /api/v1/admin/matchmaker/mutual-picks/{pick_id}/super`
专供运营手动置顶互选记录，写入审计。

> 注：互选记录页面走 `/active-signupmanager` 红娘管理账号可创建 pick，后端对 C 端走 `/api/v1/matchmaker/mutual-picks`。两个接口共用 `mutual_selection_pick` 表。

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
