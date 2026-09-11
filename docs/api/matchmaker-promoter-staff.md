# 推广红娘管理 后台 API（推广体系独立于总店红娘）

> **归属页面**：推广红娘 → 红娘管理
> **URL**：前端 `xuanshiai.com/admin/poplove-matchmaker-list`
> **后端统一前缀**：`/api/v1/admin/promoters`
> **权限点**：读 `matchmaker.read` / 写 `matchmaker.manage`
> **身份模型**：复用 `user_matchmaker_apply (application_type='promoter')`，**不加独立档案表**（与总店红娘 service_matchmaker 体系互不相干）
> **迁移**：执行 `python database_setup_marriage.py` 幂等补齐 `user_matchmaker_apply.channel` 与 `user_matchmaker_apply.visible` 列（均通过 `_ensure_required_columns` 幂等 ALTER TABLE）

---

## 一、通用约定

- **登录要求**：红娘后台登录态 `Authorization: Bearer <access_token>`；未登录 `401`。
- **身份范围**：推广红娘 = `user_matchmaker_apply.application_type='promoter'` 且 `status IN (1, 2)` 的记录
  - `status=1` 在职（列表「在职」Tab、页面状态标签「在职」）
  - `status=2` 离职（列表「离职」Tab、页面状态标签「离职」）
  - `status=0` 待审核的 C 端推广申请**不展示**在本管理页（走申请审核入口）
- **行主键 = `users.id`（user_id）**；一用户至多一条 promoter 申请（`uk(user_id, application_type)`）。
- **展示字段取数口径**：
  - 姓名/头像/手机号：`users` 表；`real_name` 优先于 `nickname` 作为展示名
  - 账号：`users.nickname`（列表「账号」列）
  - 推广渠道：`user_matchmaker_apply.channel`（仅推广红娘使用）
  - 兼职/全职：`user_matchmaker_apply.matchmaker_type`（`part_time`/`full_time`/`null`）；`matchmaker_type_label` 派生为「兼职」/「全职」/「—」
  - 分成级别：`user_matchmaker_apply.commission_level_id`（1~4）关联 `promoter_level_config`，`commission_level_name` 来自其 `level_name`
  - 隶属团队：`partner_membership.status=1` 的 `team_id` → `partner_team.name`（`team_name`）；无团队时 `team_id`/`team_name` 为 `null`
  - 推广会员数：`promotion_attribution` 中 `promoter_id = user_id AND status=1` 的去重会员计数（`member_count`）；其中 `effective_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')` 为 `member_month`（本月新增）
  - 录入客源线索数：`customer_lead` 中 `created_by = user_id` 的计数（`lead_total`）；其中 `created_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')` 为 `lead_month`（本月录入）
  - 开单明细金额合计：`commission_entry` 中 `beneficiary_type='promoter' AND beneficiary_id = user_id AND status<>'REVERSED'` 的 `SUM(amount)`（`order_amount`，字符串），无记录时返回 `"0.00"`
  - 推广分享码数：`promotion_touch` 中 `promoter_id = user_id` 的计数（`touch_count`）
  - 入职时间：`user_matchmaker_apply.reviewed_at`（后台添加时写入当前时间）
  - 是否前台展示：`user_matchmaker_apply.visible`（1 展示 / 0 隐藏），列表「是否展示」开关
- **添加推广红娘**只接受**绑定已注册普通用户**（搜索昵称/手机号）的方式，不新建 C 端用户。
- **离职/复职**通过编辑状态或 `PATCH /status` 完成：离职会写 `suspended_at = UTC_TIMESTAMP()` 与原因，复职清空离职时间。
- 分页统一返回 `items / page / page_size / total / has_more`。
- **默认排序**（等价 `sort=joined_desc`）：`COALESCE(reviewed_at, created_at) DESC`；不再使用旧的 `user_id DESC`。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/promoters` | 分页查询推广红娘（Tab/关键字/级别/团队/展示/排序） | matchmaker.read |
| 2 | GET | `/api/v1/admin/promoters/user-candidates` | 搜索可绑定普通用户 | matchmaker.read |
| 3 | POST | `/api/v1/admin/promoters` | 添加推广红娘（绑定普通用户） | matchmaker.manage |
| 4 | GET | `/api/v1/admin/promoters/{user_id}` | 推广红娘详情 | matchmaker.read |
| 5 | PUT | `/api/v1/admin/promoters/{user_id}` | 编辑推广红娘（含状态/展示） | matchmaker.manage |
| 6 | PATCH | `/api/v1/admin/promoters/{user_id}/status` | 离职/复职快捷操作 | matchmaker.manage |
| 7 | GET | `/api/v1/admin/promoters/statistics` | 推广红娘总览统计（在职/引流/线索） | matchmaker.read |
| 8 | GET | `/api/v1/admin/promoters/teams` | 在职合伙团队列表（隶属团队下拉） | matchmaker.read |
| 9 | PUT | `/api/v1/admin/promoters/{user_id}/team` | 调整推广红娘隶属团队（可移出） | matchmaker.manage |
| 10 | GET | `/api/v1/admin/promoters/commission-entries` | 推广红娘分成明细（分页） | matchmaker.read |
| 11 | GET | `/api/v1/admin/promoters/commission-entries/options` | 分成明细筛选项（红娘/事件） | matchmaker.read |
| 12 | POST | `/api/v1/admin/promoters/{user_id}/poster` | 生成推广海报（占位实现） | matchmaker.read |
| 13 | POST | `/api/v1/admin/promoters/{user_id}/platform-token` | 获取推广红娘后台 Token（跳转） | matchmaker.read |
| 14 | DELETE | `/api/v1/admin/promoters/{user_id}` | 硬删推广红娘（含校验） | matchmaker.manage |

---

## 三、详细契约

### 3.1 `GET /api/v1/admin/promoters`

**基本信息**
- 用途：分页查询推广红娘。
- Method / URL：`GET /api/v1/admin/promoters`（无请求体）。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数（query）**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验与含义 |
| --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | 页码 ≥ 1 |
| `page_size` | query | int | 否 | 20 | 每页 1~100 |
| `keyword` | query | string | 否 | — | 昵称/姓名/手机号模糊搜索，≤100 字符 |
| `status` | query | int | 否 | — | 1 在职 / 2 离职；不传 = 全部（含在职+离职） |
| `commission_level_id` | query | int | 否 | — | 1~4，按分成级别过滤 |
| `team_id` | query | int | 否 | — | ≥1，按隶属合伙团队过滤（取 `partner_membership.status=1` 的团队） |
| `visible` | query | bool | 否 | — | 是否前台展示（1/true 展示，0/false 隐藏） |
| `sort` | query | string | 否 | `joined_desc` | 排序枚举：`joined_desc` 加入时间降序（默认） / `joined_asc` 加入时间升序 / `member_desc` 名下会员数降序 / `member_asc` 名下会员数升序 |

> 4 个新增参数全部可选，向后兼容：旧客户端不传时行为与旧版一致。`sort` 默认等价旧版的 `user_id DESC` 已调整为 `COALESCE(reviewed_at, created_at) DESC`（见「一、通用约定」）。

**请求示例**

```http
GET /api/v1/admin/promoters?page=1&page_size=20&status=1&commission_level_id=2&team_id=10&visible=true&sort=member_desc
Authorization: Bearer <access_token>
```

无请求体。非法示例：
- `status=3` → `422`
- `commission_level_id=5`（超出 1~4）→ `422`
- `team_id=0`（<1）→ `422`
- `sort=unknown`（非枚举）→ `422`

**返回 200** — `PromoterStaffPage`

```json
{
  "items": [
    {
      "id": 1005,
      "user_id": 1005,
      "avatar": "https://cdn.example.com/avatar/u1005.png",
      "display_name": "张小推",
      "account": "xiaomei_1005",
      "phone": "138****5678",
      "channel": "抖音",
      "matchmaker_type": "part_time",
      "matchmaker_type_label": "兼职",
      "commission_level_id": 2,
      "commission_level_name": "推广大师",
      "team_id": 10,
      "team_name": "华东合伙团",
      "member_count": 12,
      "member_month": 3,
      "lead_total": 20,
      "lead_month": 5,
      "order_amount": "1280.00",
      "touch_count": 15,
      "status": 1,
      "status_label": "在职",
      "visible": true,
      "reviewed_at": "2026-08-01T10:00:00",
      "intro": "专注抖音同城引流",
      "created_at": "2026-08-01T09:00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

**返回字段（25 字段 `PromoterStaffItem`）**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items[].id` | int | 是 | 行主键，等于 `user_id`（页面「ID」列） |
| `items[].user_id` | int | 是 | 普通用户 ID（路径参数使用） |
| `items[].avatar` | string? | 是 | 头像 URL（页面「推广红娘」列头像） |
| `items[].display_name` | string | 是 | 展示称呼：`real_name` 优先回退 `nickname`（页面「推广红娘」-称呼） |
| `items[].account` | string? | 是 | 账号，等于 `users.nickname` |
| `items[].phone` | string? | 是 | 手机号（页面「手机号」列） |
| `items[].channel` | string? | 是 | 推广渠道（页面「推广渠道」标签） |
| `items[].matchmaker_type` | `part_time`/`full_time`/null | 是 | 兼职 / 全职 / 未设置 |
| `items[].matchmaker_type_label` | string | 是 | 「兼职」/「全职」/「—」（页面红娘类型列） |
| `items[].commission_level_id` | int? | 是 | 分成级别 1~4（页面「分成级别」列；未设置时 `null`） |
| `items[].commission_level_name` | string? | 是 | 级别名称如「推广大师」（页面「分成级别」列；来自 `promoter_level_config.level_name`） |
| `items[].team_id` | int? | 是 | 隶属团队 ID（页面「隶属团队」列；无团队 `null`） |
| `items[].team_name` | string? | 是 | 团队名称（页面「隶属团队」列；来自 `partner_membership(status=1) JOIN partner_team`） |
| `items[].member_count` | int | 是 | 名下会员总数（页面「名下会员」-共 N 人） |
| `items[].member_month` | int | 是 | 本月新增会员数（页面「名下会员」-本月 N 人） |
| `items[].lead_total` | int | 是 | 累计录入客源线索数（页面「录入客源线索」-共 N 条） |
| `items[].lead_month` | int | 是 | 本月录入客源线索数（页面「录入客源线索」-本月 N 条） |
| `items[].order_amount` | string(decimal) | 是 | 开单明细金额合计（页面「开单明细」；字符串，无记录时 `"0.00"`） |
| `items[].touch_count` | int | 是 | 推广分享码数 |
| `items[].status` | int | 是 | 1 在职 / 2 离职 |
| `items[].status_label` | string | 是 | 「在职」/「离职」（页面状态标签） |
| `items[].visible` | bool | 是 | 是否前台展示（页面「是否展示」开关；`user_matchmaker_apply.visible` 1/0） |
| `items[].reviewed_at` | datetime? | 是 | 入职时间（审核通过时间，页面「加入时间」列） |
| `items[].intro` | string? | 是 | 备注/简介 |
| `items[].created_at` | datetime? | 是 | 申请记录创建时间 |
| `page`/`page_size`/`total`/`has_more` | int/bool | 是 | 分页元信息 |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 前端 Tabs：全部=不传 `status`；在职=`status=1`；离职=`status=2`。
- 聚合字段真实口径（SQL）：
  - `member_count` = `promotion_attribution` WHERE `promoter_id = user_id` AND `status = 1` 计数
  - `member_month` = 上述条件再加 `effective_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')`
  - `lead_total` = `customer_lead` WHERE `created_by = user_id` 计数
  - `lead_month` = 上述条件再加 `created_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')`
  - `order_amount` = `commission_entry` WHERE `beneficiary_type='promoter'` AND `beneficiary_id = user_id` AND `status <> 'REVERSED'` 的 `SUM(amount)`，为 NULL 时返回 `"0.00"`
  - `commission_level_name` 来自 `promoter_level_config.level_name`
  - `team_name` 来自 `partner_membership(status=1) JOIN partner_team`
- `sort` 排序映射：
  - `joined_desc`/`joined_asc`：`COALESCE(reviewed_at, created_at)` 降序/升序
  - `member_desc`/`member_asc`：`member_count` 降序/升序
- 空结果返回 `items: []`、`total: 0`。
- 待审核推广申请（status=0）不会出现在此列表。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |
| 422 | 参数非法（status/commission_level_id/team_id/sort 等） | `{"detail":[...]}` | 按提示修正 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（分页接口含分页字段说明；无返回体时已明确标注）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、调用顺序、幂等、限流、状态流转和边界场景
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例

---

### 3.2 `GET /api/v1/admin/promoters/user-candidates`

**基本信息**
- 用途：添加推广红娘时搜索普通用户（排除已是推广红娘/有待审核推广申请的用户）。
- URL：`GET /api/v1/admin/promoters/user-candidates?keyword=xxx`（无请求体）
- 权限：`matchmaker.read`；成功 `200`。

**请求参数（query）**：`keyword` 必填，长度 2~100，按昵称或手机号模糊匹配。

**返回 200** — `List[PromoterUserCandidate]`（上限 10 条）

```json
[
  { "id": 2001, "nickname": "小美", "phone": "138****0001", "avatar": null }
]
```

**错误**：`401` / `403` / `422`（keyword 缺失或过短）。

---

### 3.3 `POST /api/v1/admin/promoters`

**基本信息**
- 用途：添加推广红娘（绑定普通用户）。绑定后写入 `user_matchmaker_apply(promoter, status=1)` 并授予 `user_role(role_code='promoter')`。
- URL：`POST /api/v1/admin/promoters`；权限：`matchmaker.manage`；成功 `201`。

**请求体** — `PromoterStaffCreate`

```json
{
  "user_id": 2001,
  "channel": "抖音",
  "intro": "同城流量推广"
}
```

| 字段 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- |
| `user_id` | int | 二选一 | ≥1 | 要绑定的普通用户 ID |
| `lookup` | string | 二选一 | 2~100 | 按昵称/手机搜索用户 |
| `lookup_by` | string | 否 | `nickname`/`phone` | lookup 的匹配字段，默认 nickname |
| `channel` | string | 否 | ≤64 | 推广渠道 |
| `phone` | string? | 否 | ≤20 | 推广红娘联系电话；不传则回退绑定用户的 `users.phone` |
| `intro` | string | 否 | ≤500 | 备注/简介 |

**请求示例（lookup 方式）**

```json
{ "lookup": "138****0001", "lookup_by": "phone", "channel": "小红书" }
```

**业务规则**
- 用户必须存在且 `users.status=1`；
- 该用户此前**未有任何** `promoter` 申请记录（含待审核），否则 `409`；
- 后端将展示名落为 `users.nickname`，手机号取 `users.phone`，`reviewed_by/reviewed_at` 由后台会话即时写入。
- `phone` 字段为新增可选字段：不传时回退绑定用户的 `users.phone`；传入时覆盖写入 `users.phone`。**修复说明**：此前 service 曾访问不存在的 `body.phone` 导致 `500`，本次已修复为安全读取。

**返回 201** — `PromoterStaffDetail`（字段见 3.4）

**错误**：`401`/`403`/`404`（用户不存在）/`409`（已是推广红娘或申请待审核）/`422`（未传 user_id/lookup）。

---

### 3.4 `GET /api/v1/admin/promoters/{user_id}`

- 用途：单个推广红娘详情（查看/编辑弹窗数据）。
- 路径参数：`user_id` ≥ 1；权限：`matchmaker.read`；成功 `200`。

**返回 200** — `PromoterStaffDetail` = `PromoterStaffItem` 基础上追加

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `real_name` | string? | 申请时的实名/姓名（回退 nickname） |
| `suspension_reason` | string? | 离职原因（status=2 时返回） |

**错误**：`404` `{"detail":"推广红娘不存在"}`。

---

### 3.5 `PUT /api/v1/admin/promoters/{user_id}`

**基本信息**
- 用途：编辑推广红娘（渠道/简介/姓名/手机号/状态/离职原因）。未传字段保留原值（null 视为未修改）。
- URL：`PUT /api/v1/admin/promoters/{user_id}`；权限：`matchmaker.manage`；成功 `200`。

**请求体** — `PromoterStaffUpdate`（全部可选）

```json
{
  "channel": "视频号",
  "intro": "调整为主打视频号",
  "display_name": "张小推",
  "phone": "139****0000",
  "visible": true,
  "status": 2,
  "reason": "个人原因离职"
}
```

**业务规则**
- `display_name` 会同步更新 `users.nickname` 与 `user_matchmaker_apply.real_name`；`phone` 同步 `users.phone`；
- `visible` 同步 `user_matchmaker_apply.visible`（1 展示 / 0 隐藏），控制列表「是否展示」开关；
- `status=2`：记 `suspended_at=UTC_TIMESTAMP()` 与原因 → 状态变「离职」；`status=1`：复职并清空离职时间；
- 只传状态时也可通过本接口离职/复职。

**返回 200** — `PromoterStaffDetail`；**错误**：`401`/`403`/`404`/`422`（校验失败）。

---

### 3.6 `PATCH /api/v1/admin/promoters/{user_id}/status`

**基本信息**
- 用途：行内快捷离职/复职。
- URL：`PATCH /api/v1/admin/promoters/{user_id}/status`；权限：`matchmaker.manage`；成功 `200`。

**请求体** — `PromoterStatusUpdate`

```json
{ "status": 2, "reason": "合作到期" }
```

**业务规则**
- `status=2` 离职（建议带 reason）；`status=1` 复职（reason 可忽略）。

**返回 200** — `PromoterStaffDetail`；**错误**：`401`/`403`/`404`/`422`。

---

### 3.7 `GET /api/v1/admin/promoters/statistics`

**基本信息**
- 用途：推广红娘管理页顶部总览统计（在职兼职/全职数、累计/本月/上月引流会员、累计/本月/上月录入线索）。
- Method / URL：`GET /api/v1/admin/promoters/statistics`（无请求体、无 query）。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数**：无。

**请求示例**

```http
GET /api/v1/admin/promoters/statistics
Authorization: Bearer <access_token>
```

无请求体。

**返回 200** — `PromoterStatistics`

```json
{
  "part_time_count": 8,
  "full_time_count": 3,
  "member_total": 520,
  "member_month": 64,
  "member_last_month": 58,
  "lead_total": 1320,
  "lead_month": 142,
  "lead_last_month": 120
}
```

空数据示例（无任何在职推广红娘时全部为 0）：

```json
{
  "part_time_count": 0,
  "full_time_count": 0,
  "member_total": 0,
  "member_month": 0,
  "member_last_month": 0,
  "lead_total": 0,
  "lead_month": 0,
  "lead_last_month": 0
}
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `part_time_count` | int | 是 | 在职兼职推广红娘数（`matchmaker_type='part_time'` 且 `status=1`） |
| `full_time_count` | int | 是 | 在职全职推广红娘数（`matchmaker_type='full_time'` 且 `status=1`） |
| `member_total` | int | 是 | 累计引流注册会员数（在职推广红娘名下 `promotion_attribution.status=1`） |
| `member_month` | int | 是 | 本月新增引流会员数 |
| `member_last_month` | int | 是 | 上月引流会员数 |
| `lead_total` | int | 是 | 累计录入客源线索数 |
| `lead_month` | int | 是 | 本月录入客源线索数 |
| `lead_last_month` | int | 是 | 上月录入客源线索数 |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- **只统计在职（`status=1`）推广红娘名下的数据**。
- `member_*` 来自 `promotion_attribution(status=1)` JOIN 在职推广红娘：
  - 本月 = `effective_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')`
  - 上月 = `effective_at >= 上月1号 AND effective_at < 本月1号`
- `lead_*` 来自 `customer_lead.created_by` JOIN 在职推广红娘：
  - 本月 = `created_at >= DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-01')`
  - 上月 = `created_at >= 上月1号 AND created_at < 本月1号`
- 全部 int，无数据时返回 `0`（不返回 `null`）。
- 该接口为纯只读聚合，无写操作、无幂等要求。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（含空数据示例）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、业务口径
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（无入参，N/A）

---

### 3.8 `GET /api/v1/admin/promoters/teams`

**基本信息**
- 用途：返回在职合伙团队列表，供「隶属团队」下拉使用。
- Method / URL：`GET /api/v1/admin/promoters/teams`（无请求体、无 query）。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数**：无。

**请求示例**

```http
GET /api/v1/admin/promoters/teams
Authorization: Bearer <access_token>
```

无请求体。

**返回 200** — `PromoterTeamItem[]`（**纯数组，不是分页**）

```json
[
  { "id": 10, "name": "华东合伙团", "owner_user_id": 2001, "owner_name": "老王", "status": 1 },
  { "id": 11, "name": "华南合伙团", "owner_user_id": 2002, "owner_name": "李姐", "status": 1 }
]
```

空数据示例（无在职团队时返回空数组）：

```json
[]
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items[].id` | int | 是 | 团队 ID |
| `items[].name` | string | 是 | 团队名称（`partner_team.name`） |
| `items[].owner_user_id` | int | 是 | 团队负责人用户 ID |
| `items[].owner_name` | string | 是 | 负责人昵称（`users.nickname`） |
| `items[].status` | int | 是 | 团队状态（仅返回 `status=1` 的在职团队） |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 只返回 `partner_team.status=1` 的团队（`status=2` 关闭团队不出现在下拉中）。
- 返回为**纯数组**，不包裹分页字段；前端直接遍历。
- 只读接口，无写操作。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（含空数据示例）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、返回结构
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（无入参，N/A）

---

### 3.9 `PUT /api/v1/admin/promoters/{user_id}/team`

**基本信息**
- 用途：调整某推广红娘的隶属团队；`team_id` 传 `null` 表示移出团队，传值时加入/调整团队。
- Method / URL：`PUT /api/v1/admin/promoters/{user_id}/team`。
- 权限：`matchmaker.manage`；成功 `200`。

**请求参数（path）**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验与含义 |
| --- | --- | --- | --- | --- | --- |
| `user_id` | path | int | 是 | — | 目标推广红娘用户 ID，≥1 |

**请求体** — `PromoterTeamUpdate`

| 字段 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- |
| `team_id` | int \| null | **必传** | 传值时 ≥1 | 目标团队 ID；`null` 表示移出当前团队（必传字段，不可省略） |
| `reason` | string? | 否 | ≤255 | 调整原因（写审计日志 `change_reason`） |

**请求示例**

```http
PUT /api/v1/admin/promoters/1005/team
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{ "team_id": 10, "reason": "转入华东合伙团" }
```

移出团队示例：

```json
{ "team_id": null, "reason": "退出合伙团" }
```

非法示例：

```json
{ "team_id": 999999 }
```

→ `400`「合伙团队不存在或已关闭」（`partner_team` 中不存在 `id=999999` 或 `status<>1`）。

**返回 200** — `PromoterStaffDetail`（`PromoterStaffItem` 25 字段 + 以下扩展字段）

| 扩展字段 | 类型 | 含义 |
| --- | --- | --- |
| `real_name` | string? | 申请时实名/姓名（回退 nickname） |
| `suspension_reason` | string? | 离职原因（status=2 时返回） |
| `slogan` | string? | 红娘口号 |
| `can_view_lead_follow` | bool | 允许查看客源线索跟进 |
| `can_write_lead_follow` | bool | 允许写客源线索跟进 |
| `can_view_member_crm_follow` | bool | 允许查看会员 CRM 跟进 |

**返回示例（调整后）**

```json
{
  "id": 1005,
  "user_id": 1005,
  "avatar": "https://cdn.example.com/avatar/u1005.png",
  "display_name": "张小推",
  "account": "xiaomei_1005",
  "phone": "138****5678",
  "channel": "抖音",
  "matchmaker_type": "part_time",
  "matchmaker_type_label": "兼职",
  "commission_level_id": 2,
  "commission_level_name": "推广大师",
  "team_id": 10,
  "team_name": "华东合伙团",
  "member_count": 12,
  "member_month": 3,
  "lead_total": 20,
  "lead_month": 5,
  "order_amount": "1280.00",
  "touch_count": 15,
  "status": 1,
  "status_label": "在职",
  "visible": true,
  "reviewed_at": "2026-08-01T10:00:00",
  "intro": "专注抖音同城引流",
  "created_at": "2026-08-01T09:00:00",
  "real_name": "张某某",
  "suspension_reason": null,
  "slogan": "同城靠谱红娘",
  "can_view_lead_follow": true,
  "can_write_lead_follow": true,
  "can_view_member_crm_follow": true
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；目标 `user_id` 必须为有效推广红娘（`status ∈ {1,2}`），否则 `404`。
- 传了 `team_id` 时校验 `partner_team` 存在且 `status=1`，否则 `400`「合伙团队不存在或已关闭」。
- 操作顺序（事务内）：
  1. 将该红娘当前 `partner_membership.status=1` 记录置为 `status=2`，并写 `left_at=UTC_TIMESTAMP()` + `change_reason` + `changed_by`；
  2. 若 `team_id` 非空，用 `INSERT ... ON DUPLICATE KEY UPDATE` 幂等写入新 `status=1` 记录（依赖 `partner_membership` 的 `UNIQUE KEY (promoter_id, status)`）；
  3. 写 `business_audit_log`：`action=promoter_staff.team.update`，`resource_type=user_matchmaker_apply`。
- 幂等：重复提交相同 `team_id` 不会产生重复 membership 记录。
- 移出团队（`team_id=null`）只执行第 1 步，不产生新记录。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `team_id` 对应团队不存在或已关闭 | `{"detail":"合伙团队不存在或已关闭"}` | 提示用户选择有效在职团队 |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.manage | `{"detail":"无权访问"}` | 提示无权限 |
| 404 | `user_id` 不是有效推广红娘 | `{"detail":"推广红娘不存在"}` | 提示目标不存在 |
| 422 | 请求体校验失败（缺 `team_id`/类型错） | `{"detail":[...]}` | 按提示修正 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（含非法示例）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、状态流转、幂等、审计
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例

---

### 3.10 `GET /api/v1/admin/promoters/commission-entries`

**基本信息**
- 用途：分页查询推广红娘分成明细（`commission_entry` 中 `beneficiary_type='promoter'`）。
- Method / URL：`GET /api/v1/admin/promoters/commission-entries`（无请求体）。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数（query）**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验与含义 |
| --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | 页码 ≥1 |
| `page_size` | query | int | 否 | 20 | 每页 1~100 |
| `promoter_id` | query | int | 否 | — | 按推广红娘过滤，≥1 |
| `rule_id` | query | int | 否 | — | 按分成规则过滤，≥1 |
| `start_date` | query | string | 否 | — | 起始日期，正则 `^\d{4}-\d{2}-\d{2}$`（如 `2026-09-01`） |
| `end_date` | query | string | 否 | — | 结束日期，正则同上 |

**请求示例**

```http
GET /api/v1/admin/promoters/commission-entries?page=1&page_size=20&promoter_id=1005&start_date=2026-09-01&end_date=2026-09-30
Authorization: Bearer <access_token>
```

无请求体。非法示例：
- `start_date=2026-9-1`（格式不符）→ `422`
- `page=0`（<1）→ `422`
- `promoter_id=0`（<1）→ `422`

**返回 200** — `PromoterCommissionEntryPage`

```json
{
  "items": [
    {
      "id": 5001,
      "created_at": "2026-09-02T14:30:00",
      "promoter_id": 1005,
      "promoter_name": "张小推",
      "promoter_avatar": "https://cdn.example.com/avatar/u1005.png",
      "consumer_id": 3001,
      "consumer_name": "王先生",
      "consumer_phone": "139****0001",
      "event_name": "会员消费分成",
      "order_id": 9001,
      "order_no": "PAY20260902001",
      "base_amount": "1280.00",
      "amount": "128.00",
      "status": "AVAILABLE"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

空数据示例：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "has_more": false
}
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items[].id` | int | 是 | 分成明细 ID |
| `items[].created_at` | datetime? | 是 | 明细创建时间 |
| `items[].promoter_id` | int | 是 | 推广红娘用户 ID |
| `items[].promoter_name` | string | 是 | 推广红娘展示名 |
| `items[].promoter_avatar` | string? | 是 | 推广红娘头像 |
| `items[].consumer_id` | int? | 是 | 消费会员用户 ID（`order_id` 关联） |
| `items[].consumer_name` | string? | 是 | 消费会员昵称 |
| `items[].consumer_phone` | string? | 是 | 消费会员手机号（脱敏） |
| `items[].event_name` | string? | 是 | 分成事件名称（`rule_id→commission_rule.name`，缺失回退 `payment_order.product_name`） |
| `items[].order_id` | int? | 是 | 关联订单 ID |
| `items[].order_no` | string? | 是 | 关联订单号 |
| `items[].base_amount` | string(decimal) | 是 | 消费金额（字符串） |
| `items[].amount` | string(decimal) | 是 | 分成金额（字符串） |
| `items[].status` | string | 是 | 枚举：`PENDING`/`AVAILABLE`/`FROZEN`/`REVERSED` |
| `page`/`page_size`/`total`/`has_more` | int/bool | 是 | 分页元信息 |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 固定筛选 `commission_entry.beneficiary_type='promoter'`。
- 日期区间**左闭右开**：`created_at >= start_date 00:00:00 AND created_at < DATE_ADD(end_date, INTERVAL 1 DAY)`。
- 排序固定 `created_at DESC, id DESC`。
- `consumer_*` 来自 `commission_entry.order_id → payment_order.user_id → users`。
- `event_name` = `commission_entry.rule_id → commission_rule.name`，缺失时回退 `payment_order.product_name`。
- 只读聚合，无写操作。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |
| 422 | 参数非法（日期格式/分页/ID 范围） | `{"detail":[...]}` | 按提示修正 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（含非法示例）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（含空数据示例）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、日期口径、排序
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例

---

### 3.11 `GET /api/v1/admin/promoters/commission-entries/options`

**基本信息**
- 用途：返回分成明细筛选下拉项（红娘列表 + 事件列表）。
- Method / URL：`GET /api/v1/admin/promoters/commission-entries/options`（无请求体、无 query）。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数**：无。

**请求示例**

```http
GET /api/v1/admin/promoters/commission-entries/options
Authorization: Bearer <access_token>
```

无请求体。

**返回 200**

```json
{
  "promoters": [
    { "id": 1005, "name": "张小推", "avatar": "https://cdn.example.com/avatar/u1005.png" },
    { "id": 1006, "name": "李小红", "avatar": null }
  ],
  "events": [
    { "id": 7, "name": "会员消费分成" },
    { "id": 8, "name": "注册奖励" }
  ]
}
```

空数据示例（无在职推广红娘/无 promoter 规则时对应数组为空）：

```json
{ "promoters": [], "events": [] }
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `promoters[].id` | int | 是 | 推广红娘用户 ID |
| `promoters[].name` | string | 是 | 展示名（`real_name` 优先回退 `nickname`） |
| `promoters[].avatar` | string? | 是 | 头像 |
| `events[].id` | int | 是 | 分成规则 ID（`commission_rule.id`） |
| `events[].name` | string | 是 | 规则名称（`commission_rule.name`） |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- `promoters` = 在职推广红娘（`status=1`），`name` 取 `real_name` 优先回退 `nickname`。
- `events` = `commission_rule` 中 `beneficiary_type='promoter' AND status=1` 的规则。
- 只读，无写操作。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（含空数据示例）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、数据来源
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（无入参，N/A）

---

### 3.12 `POST /api/v1/admin/promoters/{user_id}/poster`

**基本信息**
- 用途：获取某推广红娘的推广海报地址（扫码注册/引流用）。
- Method / URL：`POST /api/v1/admin/promoters/{user_id}/poster`。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数（path）**：`user_id` ≥1。

**请求示例**

```http
POST /api/v1/admin/promoters/1005/poster
Authorization: Bearer <access_token>
```

无请求体。

**返回 200**

```json
{
  "promoter_id": 1005,
  "url": "/storage/posters/promoter-1005.png",
  "qr_content": "promoter:1005"
}
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `promoter_id` | int | 是 | 推广红娘用户 ID |
| `url` | string | 是 | 海报图地址（当前为固定路径占位） |
| `qr_content` | string | 是 | 二维码内容（固定 `promoter:{user_id}`） |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`；目标须为有效推广红娘，否则 `404`。
- ⚠️ **占位实现**：当前接口**不生成真实海报图**，`url` 为按规则拼接的固定路径（`/storage/posters/promoter-{user_id}.png`），真实图片生成待后续实现；前端不可假定该路径已存在文件。

**错误**：`401`/`403`/`404`（推广红娘不存在）。

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，含占位实现说明
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（user_id 非整数 → 422）

---

### 3.13 `POST /api/v1/admin/promoters/{user_id}/platform-token`

**基本信息**
- 用途：获取某推广红娘的推广红娘后台登录 Token，用于跳转其独立工作台。
- Method / URL：`POST /api/v1/admin/promoters/{user_id}/platform-token`。
- 权限：`matchmaker.read`；成功 `200`。

**请求参数（path）**：`user_id` ≥1。

**请求示例**

```http
POST /api/v1/admin/promoters/1005/platform-token
Authorization: Bearer <access_token>
```

无请求体。

**返回 200**

```json
{
  "promoter_id": 1005,
  "access_token": "<access_token>",
  "refresh_token": "<refresh_token>",
  "token_type": "bearer",
  "expires_in": 3600,
  "jump_url": "/matchmaker/workbench?token=<access_token>"
}
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `promoter_id` | int | 是 | 推广红娘用户 ID |
| `access_token` | string | 是 | 后台访问 Token（占位符，真实值由后端签发） |
| `refresh_token` | string | 是 | 刷新 Token |
| `token_type` | string | 是 | 固定 `bearer` |
| `expires_in` | int | 是 | Access Token 有效秒数 |
| `jump_url` | string | 是 | 跳转地址（见下方说明） |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 若该推广红娘无对应独立后台账号，返回 `404`「推广红娘后台账号不存在」。
- ⚠️ **待定说明**：`jump_url` 当前复用总店红娘工作台路径 `/matchmaker/workbench?token=...`，**推广红娘独立工作台地址待定**，后续可能调整；前端应以下发 `jump_url` 为准，不要硬编码。

**错误**：`401`/`403`/`404`（推广红娘不存在 / 后台账号不存在）。

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，含待定说明
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（user_id 非整数 → 422）

---

### 3.14 `DELETE /api/v1/admin/promoters/{user_id}`

**基本信息**
- 用途：硬删某推广红娘记录（移除 `user_matchmaker_apply` 的 promoter 记录 + `user_role` 的 promoter 角色）。
- Method / URL：`DELETE /api/v1/admin/promoters/{user_id}`。
- 权限：`matchmaker.manage`；成功 `200`。

**请求参数（path）**：`user_id` ≥1。

**请求示例**

```http
DELETE /api/v1/admin/promoters/1005
Authorization: Bearer <access_token>
```

无请求体。

**返回 200**

```json
{ "id": 1005, "deleted": true }
```

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `id` | int | 是 | 被删除的推广红娘用户 ID |
| `deleted` | bool | 是 | 固定 `true` |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；目标须为有效推广红娘，否则 `404`。
- 存在任一约束即返回 `409`「该推广红娘名下存在会员归属或客源线索，无法删除」：
  - 名下会员归属：`promotion_attribution.status=1 AND promoter_id=user_id` 有记录；
  - 录入过客源线索：`customer_lead.created_by=user_id` 有记录。
- 实现为硬删：`DELETE FROM user_matchmaker_apply WHERE ... promoter` + `DELETE FROM user_role WHERE role_code='promoter' AND user_id=...`。
- 删除不可逆，请前端在调用前二次确认。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.manage | `{"detail":"无权访问"}` | 提示无权限 |
| 404 | 推广红娘不存在 | `{"detail":"推广红娘不存在"}` | 提示目标不存在 |
| 409 | 存在名下会员归属或客源线索 | `{"detail":"该推广红娘名下存在会员归属或客源线索，无法删除"}` | 提示先转移/清退归属后再删 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、409 约束、硬删不可逆
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（user_id 非整数 → 422）

---

## 四、迁移与验证

1. **迁移**：执行 `python database_setup_marriage.py`：
   - 新库 `CREATE TABLE user_matchmaker_apply` 已含 `channel` + 6 个业务字段；
   - 老库通过 `_ensure_required_columns` 幂等 `ALTER TABLE` 补齐 7 列（`channel` + `matchmaker_type` / `slogan` / `commission_level_id` / 3 权限 bool）。
2. **测试**：`tests/test_promoter_staff_admin.py` → `uv run pytest -q`（12 passed）。
3. **关联表**：`users`、`user_matchmaker_apply`、`user_role(role_code='promoter')`、`promotion_attribution`、`promotion_touch`。

---

## 五、补充业务字段（与图 1 添加/编辑弹窗对齐）

推广红娘弹窗除基础身份字段外还含以下 5 块业务配置，**仅 promoter 记录使用**，service_matchmaker/partner 申请不填（共享 `user_matchmaker_apply` 表，但只对 promoter 有意义）：

| 字段 | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_type` | `part_time` / `full_time` | `part_time` | 红娘类型：兼职 / 全职 |
| `slogan` | varchar(128) | `null` | 红娘口号（预设 + 自定义） |
| `commission_level_id` | int(1~4) | `null` | 分成级别：1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使 |
| `can_view_lead_follow` | bool | `true` | 允许查看客源线索跟进记录 |
| `can_write_lead_follow` | bool | `true` | 允许在客源线索中写跟进 |
| `can_view_member_crm_follow` | bool | `true` | 允许查看会员 CRM 跟进记录 |

> 注：`commission_level_id` 当前直接存整数 1~4（不引用独立 `promoter_commission_level` 表），后续如需独立配置表可平滑迁移（参见「分成配置」扩展计划）。

---

## 六、变更记录

> 本次为增量补充，接口路径与全局约定不变，主要为列表聚合字段扩展、新增统计/团队/分成明细/海报/Token/删除等端点。所有新增 query/字段均向后兼容。

### 2026-09-11 推广红娘管理接口扩展

**变更前**
- `GET /api/v1/admin/promoters`：4 个 query 参数（`page`/`page_size`/`keyword`/`status`），返回 `PromoterStaffItem` 13 字段，默认排序 `user_id DESC`。
- 接口清单 6 条，无统计/团队/分成明细/海报/Token/删除端点。
- `POST /api/v1/admin/promoters` 请求体无 `phone`；`PUT /api/v1/admin/promoters/{user_id}` 请求体无 `visible`。
- 迁移提示仅补齐 `channel` 列；通用约定未定义 `visible`/`隶属团队` 取数口径。

**变更后**
- `GET /api/v1/admin/promoters`：query 扩展至 8 个（新增 `commission_level_id`/`team_id`/`visible`/`sort`，全部可选）；返回 `PromoterStaffItem` 扩展至 25 字段（新增 `account`/`matchmaker_type`/`matchmaker_type_label`/`commission_level_id`/`commission_level_name`/`team_id`/`team_name`/`member_month`/`lead_total`/`lead_month`/`order_amount`/`visible`）；默认排序改为 `COALESCE(reviewed_at, created_at) DESC`（`sort=joined_desc`）。
- 接口清单扩展至 14 条，新增：
  - `GET /statistics`（总览统计）
  - `GET /teams`（在职合伙团队）
  - `PUT /{user_id}/team`（调整隶属团队，可移出）
  - `GET /commission-entries`（分成明细分页）
  - `GET /commission-entries/options`（筛选项）
  - `POST /{user_id}/poster`（海报占位）
  - `POST /{user_id}/platform-token`（后台 Token 跳转）
  - `DELETE /{user_id}`（硬删，带 409 校验）
- `POST /api/v1/admin/promoters` 请求体新增可选 `phone`（≤20，不传回退绑定用户手机号）；修复此前访问不存在 `body.phone` 导致 `500` 的问题。
- `PUT /api/v1/admin/promoters/{user_id}` 请求体新增可选 `visible`（是否前台展示）。
- 通用约定补充 `visible` 字段（1 展示/0 隐藏）、`隶属团队` 取数口径（`partner_membership.status=1 → partner_team.name`）。
- 迁移提示升级：补齐 `channel` + `visible` 两列。

**影响范围**
- 前端：列表页需适配 25 字段（新增「账号/分成级别/隶属团队/名下会员月增/客源线索/开单明细/是否展示」等列）；新增统计卡、团队下拉、分成明细页、海报/Token/删除入口。
- 旧客户端：不传新增 query/字段时行为兼容，无需强制升级。
- 金额类字段（`order_amount`/`base_amount`/`amount`）为字符串，前端需 `Number()` 解析。
- `jump_url`（platform-token）与 `url`（poster）当前为占位/复用路径，推广红娘独立工作台地址待定，前端以下发值为准。
