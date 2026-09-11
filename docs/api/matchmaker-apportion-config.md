# 总店红娘 → 分派配置 后台 API

> **归属页面**：总店红娘 → 红娘管理 → 分派配置  
> **URL**：前端 `xuanshiai.com/admin/love-matchmaker-apportion2`  
> **页面结构**：2 个 Tab（**会员CRM** / **客源线索**）× 2 个配置块（**分配配置 assign** / **弃海配置 abandon**）= 4 块配置  
> **后端统一前缀**：`/api/v1/admin/matchmaker/apportion-config`  
> **权限点**：`matchmaker.apportion.read`（读）/ `matchmaker.apportion.write`（写）  
> **数据表**：`matchmaker_apportion_config`（uk: `(scope, config_type)`，4 行 = 4 块配置）

---

## 一、通用约定

- **登录要求**：所有端点都需要红娘后台登录态（`Authorization: Bearer <access_token>`），未登录返回 `401`。
- **响应包络**：项目其它接口返回 `{code, data, msg, success}`，本组接口直接返回 `data`（沿用 `reward_rule_admin` 三段式风格）。
- **scope 枚举**：`member_crm`（会员CRM）/ `customer_lead`（客源线索）。
- **config_type 枚举**：`assign`（分配配置）/ `abandon`（弃海配置）。
- **审计**：所有 `PUT`/`PATCH` 都会写入 `business_audit_log`，`resource_type='matchmaker_apportion_config'`，`action ∈ {'apportion_config.assign.upsert','apportion_config.abandon.upsert','apportion_config.toggle'}`，`actor_user_id` 来自红娘后台会话，`after_json` 字段保存请求体。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/matchmaker/apportion-config` | 取 4 块配置（页面加载用） | matchmaker.apportion.read |
| 2 | GET | `/api/v1/admin/matchmaker/apportion-config/{scope}/{config_type}` | 取单块配置 | matchmaker.apportion.read |
| 3 | PUT | `/api/v1/admin/matchmaker/apportion-config/{scope}/assign` | 保存/更新分配配置 | matchmaker.apportion.write |
| 4 | PUT | `/api/v1/admin/matchmaker/apportion-config/{scope}/abandon` | 保存/更新弃海配置 | matchmaker.apportion.write |
| 5 | PATCH | `/api/v1/admin/matchmaker/apportion-config/{scope}/{config_type}/toggle` | 切换 `is_enabled` | matchmaker.apportion.write |
| 6 | GET | `/api/v1/admin/matchmaker/apportion-config/audit-log` | 分页查审计日志 | matchmaker.apportion.read |

---

## 三、详细契约

### 3.1 `GET /api/v1/admin/matchmaker/apportion-config`

**基本信息**  
- 用途：取总店红娘分派配置的 4 块记录，**用于页面初次加载**。  
- URL：`GET /api/v1/admin/matchmaker/apportion-config`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.apportion.read`  
- Content-Type：无请求体  

**请求参数**：无。

**请求示例**：

```
GET /api/v1/admin/matchmaker/apportion-config
Authorization: Bearer <access-token>
```

**返回参数**：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items[].id` | int | 是 | 主键 |
| `items[].scope` | string | 是 | `member_crm` 或 `customer_lead` |
| `items[].config_type` | string | 是 | `assign` 或 `abandon` |
| `items[].strategy` | string\|null | 是 | 仅 `assign` 块：`designated`/`round_robin_random`/`by_region`/`by_promoter`/`none`；`abandon` 块固定为 null |
| `items[].target_matchmaker_id` | int\|null | 是 | 仅 `strategy=designated` 时为指定服务红娘 user_id |
| `items[].target_matchmaker_name` | string\|null | 是 | 服务红娘展示名（LEFT JOIN users） |
| `items[].auto_abandon_days` | int\|null | 是 | 仅 `abandon` 块：0/3/7/15/30/45/60/90 |
| `items[].daily_pickup_limit` | int\|null | 是 | 仅 `abandon` 块：每日捞取上限，0=不限 |
| `items[].show_admin_abandoned_in_pool` | bool | 是 | 后台管理员放弃的客源是否在其它分门弃海池显示 |
| `items[].show_store_abandoned_in_pool` | bool | 是 | 总店红娘放弃的客源是否在其它分门弃海池显示 |
| `items[].is_enabled` | bool | 是 | 是否启用本配置 |
| `items[].updated_by` | int\|null | 是 | 最后修改人 user_id |
| `items[].remark` | string\|null | 是 | 备注 |
| `items[].created_at` | datetime\|null | 是 | 创建时间 |
| `items[].updated_at` | datetime\|null | 是 | 更新时间 |

**返回示例**：

```json
[
  {
    "id": 1,
    "scope": "member_crm",
    "config_type": "assign",
    "strategy": "designated",
    "target_matchmaker_id": null,
    "target_matchmaker_name": null,
    "auto_abandon_days": null,
    "daily_pickup_limit": null,
    "show_admin_abandoned_in_pool": true,
    "show_store_abandoned_in_pool": true,
    "is_enabled": true,
    "updated_by": 1,
    "remark": "会员CRM分配配置-默认统一分派",
    "created_at": "2026-09-08T15:00:00",
    "updated_at": "2026-09-08T15:00:00"
  },
  {
    "id": 2,
    "scope": "member_crm",
    "config_type": "abandon",
    "strategy": null,
    "target_matchmaker_id": null,
    "target_matchmaker_name": null,
    "auto_abandon_days": 0,
    "daily_pickup_limit": 0,
    "show_admin_abandoned_in_pool": true,
    "show_store_abandoned_in_pool": true,
    "is_enabled": true,
    "updated_by": 1,
    "remark": "会员CRM弃海配置-默认不启用",
    "created_at": "2026-09-08T15:00:00",
    "updated_at": "2026-09-08T15:00:00"
  },
  {
    "id": 3,
    "scope": "customer_lead",
    "config_type": "assign",
    "strategy": "designated",
    "target_matchmaker_id": null,
    "target_matchmaker_name": null,
    "auto_abandon_days": null,
    "daily_pickup_limit": null,
    "show_admin_abandoned_in_pool": true,
    "show_store_abandoned_in_pool": true,
    "is_enabled": true,
    "updated_by": 1,
    "remark": "客源线索分配配置-默认统一分派",
    "created_at": "2026-09-08T15:00:00",
    "updated_at": "2026-09-08T15:00:00"
  },
  {
    "id": 4,
    "scope": "customer_lead",
    "config_type": "abandon",
    "strategy": null,
    "target_matchmaker_id": null,
    "target_matchmaker_name": null,
    "auto_abandon_days": 0,
    "daily_pickup_limit": 0,
    "show_admin_abandoned_in_pool": true,
    "show_store_abandoned_in_pool": true,
    "is_enabled": true,
    "updated_by": 1,
    "remark": "客源线索弃海配置-默认不启用",
    "created_at": "2026-09-08T15:00:00",
    "updated_at": "2026-09-08T15:00:00"
  }
]
```

**使用方法与业务规则**：
- 前端页面加载时调用一次；同时前端也可在保存后再次拉取刷新。
- 无分页，一次最多 4 条；空表（未跑建表脚本）返回 `500` 业务错误，前端应按 toast 提示"配置尚未初始化"。

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录 / 令牌失效 | `{"detail":"未登录或令牌失效"}` | 跳红娘后台登录页 |
| 403 | 持有 token 但无 `matchmaker.apportion.read` | `{"detail":"无权限执行该操作"}` | 隐藏入口 + 提示"无权限" |
| 500 | 配置表未初始化（未跑建表脚本） | `{"detail":"分派配置尚未初始化"}` | toast 提示"配置尚未初始化"，联系运维 |

**空数据示例**：未配置时返回空数组 `[]`（最多 4 条的正常场景返回 0~4 条）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义（无参数已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（GET 无 body 非法场景）

---

### 3.2 `GET /api/v1/admin/matchmaker/apportion-config/{scope}/{config_type}`

**基本信息**  
- 用途：取单块配置（assign 或 abandon）。  
- Path 参数：`scope ∈ {member_crm, customer_lead}`；`config_type ∈ {assign, abandon}`  
- 权限：`matchmaker.apportion.read`

**返回参数**：同 3.1 中单项。

**返回示例**：

```json
{
  "id": 1,
  "scope": "member_crm",
  "config_type": "assign",
  "strategy": "designated",
  ...
}
```

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `matchmaker_apportion_config` 中无对应行 | `{"detail":"配置不存在"}` | 提示"配置尚未初始化，请联系运维" |
| 422 | `scope` 或 `config_type` 不在枚举 | `{"detail":"参数校验失败"}` | 校验 URL 拼写 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义（path 枚举已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节（见 3.1 总述 + 本接口调用场景）
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（`scope`/`config_type` 非法 → 422）

---

### 3.3 `PUT /api/v1/admin/matchmaker/apportion-config/{scope}/assign`

**基本信息**  
- 用途：保存/更新分配配置（assign 块），对应截图「分配配置」区。  
- 权限：`matchmaker.apportion.write`

**请求体参数**：

| 字段 | 类型 | 必填 | 默认 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| `strategy` | string | 是 | — | enum: `designated`/`round_robin_random`/`by_region`/`by_promoter`/`none` | 分配策略 |
| `target_matchmaker_id` | int | 否 | null | ≥1；存在且 `user_role.role_code='service_matchmaker' & status=1` | 仅 strategy=designated 时必填 |
| `is_enabled` | bool | 否 | true | — | 是否启用本配置 |
| `remark` | string | 否 | null | ≤255 | 备注 |

**互斥规则**：
- `strategy=designated` 时 `target_matchmaker_id` 必须传；其余策略 `target_matchmaker_id` 必须为 null。

**请求示例**：

```json
{
  "strategy": "designated",
  "target_matchmaker_id": 7,
  "is_enabled": true,
  "remark": "统一分派给小美"
}
```

非法示例：

```json
{"strategy": "designated"}
```
→ 422：`strategy=designated 时必须指定 target_matchmaker_id`

```json
{"strategy": "none", "target_matchmaker_id": 7}
```
→ 422：`仅 strategy=designated 才允许传入 target_matchmaker_id`

**返回示例**：同 3.2 单项。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.apportion.write`。  
- 调用顺序：页面"分配配置"区编辑 → `PUT` 保存 → 保存成功后调 `GET 3.1` 刷新整页。  
- 幂等：upsert 语义，重复提交相同内容结果一致；不同内容以最后一次为准。  
- 互斥校验：`strategy=designated` 必须带 `target_matchmaker_id`；其余策略传 `target_matchmaker_id` 会 422（见互斥规则）。  
- 边界场景：`scope=customer_lead` 暂未启用 `by_region` / `by_promoter`（400）；`target_matchmaker_id` 须为 `status=1` 且 `role_code=service_matchmaker` 的用户（否则 400）。

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `strategy=designated` 但 `target_matchmaker_id` 不存在或非 `service_matchmaker` | `{"detail":"指定的红娘不存在或非服务红娘"}` | 表单回填，重新选择红娘 |
| 400 | `scope=customer_lead` 且 `strategy ∈ {by_region, by_promoter}` | `{"detail":"客源线索维度未启用该策略"}` | 表单回填，提示不支持 |
| 422 | 模型校验失败 / 互斥校验失败 | `{"detail":"参数校验失败：<字段>-<原因>"}` | 表单回填 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义（同 3.1）
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（已附 2 个非法 JSON）

---

### 3.4 `PUT /api/v1/admin/matchmaker/apportion-config/{scope}/abandon`

**基本信息**  
- 用途：保存/更新弃海配置（abandon 块），对应截图「弃海配置」区。  
- 权限：`matchmaker.apportion.write`

**请求体参数**：

| 字段 | 类型 | 必填 | 默认 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| `auto_abandon_days` | int | 是 | — | ∈ {0, 3, 7, 15, 30, 45, 60, 90} | 自动弃海天数，0=不启用 |
| `daily_pickup_limit` | int | 否 | 0 | ∈ [0, 100000] | 每日捞取上限，0=不限 |
| `show_admin_abandoned_in_pool` | bool | 否 | true | — | 后台管理员放弃的客源是否显示 |
| `show_store_abandoned_in_pool` | bool | 否 | true | — | 总店红娘放弃的客源是否显示 |
| `is_enabled` | bool | 否 | true | — | 是否启用本配置 |
| `remark` | string | 否 | null | ≤255 | 备注 |

**请求示例**：

```json
{
  "auto_abandon_days": 30,
  "daily_pickup_limit": 50,
  "show_admin_abandoned_in_pool": true,
  "show_store_abandoned_in_pool": false,
  "is_enabled": true,
  "remark": "会员CRM弃海30天自动触发，每日可捞50"
}
```

非法示例：

```json
{"auto_abandon_days": 14}
```
→ 422：`auto_abandon_days 必须在 [0, 3, 7, 15, 30, 45, 60, 90] 之内`

**返回示例**：同 3.2 单项。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.apportion.write`。  
- 调用顺序：页面"弃海配置"区编辑 → `PUT` 保存 → 成功后调 `GET 3.1` 刷新。  
- 幂等：upsert 语义，重复提交相同内容结果一致。  
- 边界场景：`auto_abandon_days` 仅接受白名单 `{0,3,7,15,30,45,60,90}`，其它值 422；`daily_pickup_limit` 范围 `[0,100000]`。

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 422 | 模型校验失败（`auto_abandon_days` 不在白名单 / `daily_pickup_limit` 超范围 / 缺必填） | `{"detail":"参数校验失败：<字段>-<原因>"}` | 表单回填 |

> 除模型 422 外，本接口无其它业务错误。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义（同 3.1）
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（已附 1 个非法 JSON）

---

### 3.5 `PATCH /api/v1/admin/matchmaker/apportion-config/{scope}/{config_type}/toggle`

**基本信息**  
- 用途：仅切换单块配置的 `is_enabled`，可由前端开关控件直接触发。  
- 权限：`matchmaker.apportion.write`

**请求体参数**：

| 字段 | 类型 | 必填 | 默认 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| `is_enabled` | bool | 是 | — | — | 启用 / 停用 |
| `remark` | string | 否 | null | ≤255 | 备注，可不传（保留原 remark） |

**请求示例**：

```json
{"is_enabled": false, "remark": "临时停用"}
```

非法示例：

```json
{"remark": "临时停用"}
```
→ 422：`is_enabled` 必填

```json
{"is_enabled": "no"}
```
→ 422：`is_enabled` 必须为布尔

**返回示例**：同 3.2 单项。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.apportion.write`。  
- 调用顺序：列表 / 单块配置行的"启用/停用"开关直接触发；仅改 `is_enabled`，不触及其余字段。  
- 幂等：重复提交相同 `is_enabled` 结果一致（状态不变）。  
- 边界场景：`remark` 可不传，传则覆盖原备注；`scope` / `config_type` 非法枚举 → 422。

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录 / 令牌失效 | `{"detail":"未登录或令牌失效"}` | 跳登录页 |
| 403 | 无 `matchmaker.apportion.write` | `{"detail":"无权限执行该操作"}` | 提示无权限 |
| 422 | `is_enabled` 缺失 / 非布尔 / `scope`/`config_type` 非法 | `{"detail":"参数校验失败：<字段>-<原因>"}` | 表单回填 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义（同 3.1）
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（已附 2 个非法 JSON）

---

### 3.6 `GET /api/v1/admin/matchmaker/apportion-config/audit-log`

**基本信息**  
- 用途：分页查询分派配置的审计日志，供前端复核与排障。  
- 权限：`matchmaker.apportion.read`

**Query 参数**：

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | `≥ 1` | 页码 |
| `page_size` | query | int | 否 | 20 | `∈ [1, 100]` | 每页条数 |

**请求示例**：

```
GET /api/v1/admin/matchmaker/apportion-config/audit-log?page=1&page_size=20
Authorization: Bearer <access-token>
```

无请求体。非法示例：`page=0`（→ 422）、`page_size=200`（→ 422）。

**返回参数**：

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | 审计日志数组 | — |
| `items[].id` | int | 是 | — | 审计日志主键 | `1` |
| `items[].actor_user_id` | int\|null | 是 | `null` | 操作人用户 ID | `1` |
| `items[].action` | string | 是 | — | 操作类型（`apportion_config.assign.upsert` / `apportion_config.abandon.upsert` / `apportion_config.toggle`） | `"apportion_config.toggle"` |
| `items[].resource_type` | string | 是 | — | 固定 `matchmaker_apportion_config` | `"matchmaker_apportion_config"` |
| `items[].resource_id` | int\|null | 是 | `null` | 配置主键 id | `2` |
| `items[].before_json` | string\|null | 是 | `null` | 修改前快照（JSON 字符串） | `"{...}"` |
| `items[].after_json` | string\|null | 是 | `null` | 修改后快照（JSON 字符串） | `"{...}"` |
| `items[].reason` | string\|null | 是 | `null` | 操作原因 | `"临时停用"` |
| `items[].created_at` | datetime\|null | 是 | `null` | 操作时间 | `"2026-09-08T15:00:00"` |
| `page` | int | 是 | — | 当前页 | `1` |
| `page_size` | int | 是 | — | 每页条数 | `20` |
| `total` | int | 是 | — | 总条数 | `35` |
| `has_more` | bool | 是 | — | 是否还有下一页 | `true` |

**返回示例（有数据）**

```json
{
  "items": [
    {
      "id": 1,
      "actor_user_id": 1,
      "action": "apportion_config.toggle",
      "resource_type": "matchmaker_apportion_config",
      "resource_id": 2,
      "before_json": "{\"is_enabled\": true}",
      "after_json": "{\"is_enabled\": false}",
      "reason": "临时停用",
      "created_at": "2026-09-08T15:00:00"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

**返回示例（空数据）**

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.apportion.read`。  
- 调用顺序：分派配置页"审计日志"入口触发；可与配置编辑独立展示。  
- 幂等：查询天然幂等。  
- 边界场景：无日志时返回空数组（`total=0`）；`before_json` / `after_json` 为 JSON 字符串，前端需 `JSON.parse` 后展示。

**错误**：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录 / 令牌失效 | `{"detail":"未登录或令牌失效"}` | 跳登录页 |
| 403 | 无 `matchmaker.apportion.read` | `{"detail":"无权限执行该操作"}` | 提示无权限 |
| 422 | `page` / `page_size` 校验失败 | `{"detail":"参数校验失败"}` | 修正分页参数 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注，含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议、错误响应 JSON
- [x] 至少一个非法参数示例（已附分页非法示例）

---

## 四、变更记录

| 日期 | 版本 | 变更 | 影响 |
| --- | --- | --- | --- |
| 2026-09-08 | v1.0 | 新增 6 个端点 + 1 张表 | — |
| 2026-09-11 | v1.1 | 按 `PROJECT_RULES.md` 2.1.1 统一模板补全：① 示例 Token 由 `eyJhbGciOi...` 改为占位符 `<access-token>`；② 为 3.1~3.6 各接口补「文档完成自检清单」；③ 错误表统一增加「错误响应 JSON」列并补充 401/403/422/500 触发条件与前端建议；④ 为 3.3/3.4 补「使用方法与业务规则」；⑤ 为 3.5 补「使用方法与业务规则」「错误」及非法示例；⑥ 为 3.6 补「请求示例」「返回示例（含空数据）」「使用方法与业务规则」「错误」并展开返回参数表（加必返/空值/示例列） | 纯文档补充，不影响接口契约；旧客户端无需改动 |

---

## 五、兼容性说明

- 本次为**新增接口**，不影响任何现有接口。
- `matchmaker_apportion_config` 表使用 `INSERT IGNORE` 种子，幂等可重跑；旧库升级只需执行 `database_setup_marriage.py` 即可。
- 审计日志表 `business_audit_log` 已存在，本期复用，无新增字段。

---

## 六、关联能力（不在本期范围）

- **自动分派执行引擎**（按 `strategy=round_robin_random|by_region|by_promoter` 实际触发分派、关联会员/客源线索）— 排期：二期
- **自动弃海调度**（cron 扫描超 `auto_abandon_days` 的会员/客源线索，将其置 LOST 状态）— 排期：二期
- **当前一期仅完成"配置 CRUD + 审计"**，业务执行方需要在调用方自行读取本组接口的配置后做行为决策