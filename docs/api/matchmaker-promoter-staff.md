# 推广红娘管理 后台 API（推广体系独立于总店红娘）

> **归属页面**：推广红娘 → 红娘管理
> **URL**：前端 `xuanshiai.com/admin/poplove-matchmaker-list`
> **后端统一前缀**：`/api/v1/admin/promoters`
> **权限点**：读 `matchmaker.read` / 写 `matchmaker.manage`
> **身份模型**：复用 `user_matchmaker_apply (application_type='promoter')`，**不加独立档案表**（与总店红娘 service_matchmaker 体系互不相干）
> **迁移**：执行 `python database_setup_marriage.py` 幂等补齐 `user_matchmaker_apply.channel` 列

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
  - 推广渠道：`user_matchmaker_apply.channel`（仅推广红娘使用）
  - 推广会员数：`promotion_attribution` 中 `promoter_id = user_id AND status=1` 的去重会员计数
  - 推广分享码数：`promotion_touch` 中 `promoter_id = user_id` 的计数
  - 入职时间：`user_matchmaker_apply.reviewed_at`（后台添加时写入当前时间）
- **添加推广红娘**只接受**绑定已注册普通用户**（搜索昵称/手机号）的方式，不新建 C 端用户。
- **离职/复职**通过编辑状态或 `PATCH /status` 完成：离职会写 `suspended_at = UTC_TIMESTAMP()` 与原因，复职清空离职时间。
- 分页统一返回 `items / page / page_size / total / has_more`，排序 `user_id DESC`。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/promoters` | 分页查询推广红娘（支持 Tab/关键字） | matchmaker.read |
| 2 | GET | `/api/v1/admin/promoters/user-candidates` | 搜索可绑定普通用户 | matchmaker.read |
| 3 | POST | `/api/v1/admin/promoters` | 添加推广红娘（绑定普通用户） | matchmaker.manage |
| 4 | GET | `/api/v1/admin/promoters/{user_id}` | 推广红娘详情 | matchmaker.read |
| 5 | PUT | `/api/v1/admin/promoters/{user_id}` | 编辑推广红娘（含状态） | matchmaker.manage |
| 6 | PATCH | `/api/v1/admin/promoters/{user_id}/status` | 离职/复职快捷操作 | matchmaker.manage |

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

**请求示例**

```http
GET /api/v1/admin/promoters?page=1&page_size=20&status=1&keyword=张
Authorization: Bearer <access_token>
```

无请求体。非法示例：`status=3` → `422`。

**返回 200** — `PromoterStaffPage`

```json
{
  "items": [
    {
      "id": 1005,
      "user_id": 1005,
      "avatar": "https://cdn.example.com/avatar/u1005.png",
      "display_name": "张小推",
      "phone": "138****5678",
      "channel": "抖音",
      "member_count": 12,
      "touch_count": 15,
      "status": 1,
      "status_label": "在职",
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

**返回字段**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items[].id` | int | 是 | 行主键，等于 `user_id`（页面「编号」列） |
| `items[].user_id` | int | 是 | 普通用户 ID（路径参数使用） |
| `items[].avatar` | string? | 是 | 头像 URL（页面「姓名」列头像） |
| `items[].display_name` | string | 是 | 展示名：`real_name` 优先回退 `nickname`（页面「姓名」列） |
| `items[].phone` | string? | 是 | 手机号（页面「手机号」列） |
| `items[].channel` | string? | 是 | 推广渠道（页面「推广渠道」标签） |
| `items[].member_count` | int | 是 | 推广会员数（有效推广归属去重计数，页面同名列） |
| `items[].touch_count` | int | 是 | 推广分享码数 |
| `items[].status` | int | 是 | 1 在职 / 2 离职 |
| `items[].status_label` | string | 是 | `在职` / `离职`（页面状态标签） |
| `items[].reviewed_at` | datetime? | 是 | 入职时间（审核通过时间，后台添加即时写入） |
| `items[].intro` | string? | 是 | 备注/简介 |
| `items[].created_at` | datetime? | 是 | 申请记录创建时间 |
| `page`/`page_size`/`total`/`has_more` | int/bool | 是 | 分页元信息 |

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 前端 Tabs：全部=不传 `status`；在职=`status=1`；离职=`status=2`。
- 空结果返回 `items: []`、`total: 0`。
- 待审核推广申请（status=0）不会出现在此列表。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |
| 422 | status 非法 | `{"detail":[...]}` | 按提示修正 |

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
| `intro` | string | 否 | ≤500 | 备注/简介 |

**请求示例（lookup 方式）**

```json
{ "lookup": "138****0001", "lookup_by": "phone", "channel": "小红书" }
```

**业务规则**
- 用户必须存在且 `users.status=1`；
- 该用户此前**未有任何** `promoter` 申请记录（含待审核），否则 `409`；
- 后端将展示名落为 `users.nickname`，手机号取 `users.phone`，`reviewed_by/reviewed_at` 由后台会话即时写入。

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
  "status": 2,
  "reason": "个人原因离职"
}
```

**业务规则**
- `display_name` 会同步更新 `users.nickname` 与 `user_matchmaker_apply.real_name`；`phone` 同步 `users.phone`；
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
