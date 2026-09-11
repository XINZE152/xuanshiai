# 总店红娘 → 红娘管理 后台 API

> **归属页面**：总店红娘 → 红娘管理（含菜单权限、工作报表、海报、平台免登、字典、通用上传）  
> **后端统一前缀**：`/api/v1`（下文 URL 省略此前缀）  
> **鉴权**：独立红娘后台 Token，`Authorization: Bearer <access-token>`，鉴权函数 `get_current_matchmaker_admin`；未登录 / 失效返回 `401`。  
> **权限点**：
> - 红娘管理主接口（列表 / 用户候选 / 新增 / 教程 / 详情 / 编辑 / 删除 / 锁定 / 展示 / 菜单树 / 权限 / 报表 / 海报 / 平台免登）：`matchmaker.manage`
> - 字典接口（`/admin/dict/*`，分成级别字典、门店字典）：`matchmaker.read`
> - 通用上传（`/admin/common/upload`）：`matchmaker.manage`
>
> **响应规则**：
> - 成功响应**不包 `data`** 信封，直接返回业务对象 / 数组 / 分页对象。
> - 错误统一 `HTTPException` → 响应体 `{"detail": "..."}`，**无业务错误码字段**（无 `code` / `success` / `msg` 之类信封）。
> - 分页统一结构：`items / page / page_size / total / has_more`。
> - **金额 / 比例 Decimal 一律序列化为字符串**（如 `"10.0000"`、`"59.80"`、`"0.1500"`），前端用 `Number()` / `parseFloat()` 解析，避免精度丢失。
> - 敏感字段（Token、手机号等）在示例中一律使用占位符，无真实密钥。

---

## 一、通用约定

- **登录要求**：所有端点都需要红娘后台登录态；未登录返回 `401 {"detail":"..."}`。  
- **Content-Type**：有请求体（`POST`/`PUT`/`PATCH`）的接口 `Content-Type: application/json`；上传接口 `multipart/form-data`；其余 `GET`/`DELETE` 无请求体。  
- **响应 Content-Type**：`application/json; charset=utf-8`。  
- **时间格式**：时间字段（如 `created_at`、`updated_at`、`lock_at`）为 ISO-8601 字符串（如 `2026-09-08T15:00:00`）。  
- **通用错误**（`matchmaker.manage` / `matchmaker.read` 接口共用）：

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未携带 / 非法 / 过期 Token | `{"detail":"未登录或登录已过期"}` | 跳转红娘后台登录页重新登录 |
| 403 | Token 有效但无 `matchmaker.manage`（或对应 `matchmaker.read`）权限 | `{"detail":"无权限执行该操作"}` | 隐藏入口 + 提示"无权限"，联系管理员授权 |
| 422 | 请求体 / 路径 / query 参数校验失败（类型、范围、枚举、互斥依赖） | `{"detail":"参数校验失败：<字段>-<原因>"}` 或 Pydantic 列表 | 表单回填，按提示修正后重试 |
| 404 | 资源不存在（如 `matchmaker_id` 不存在） | `{"detail":"资源不存在"}` | 提示"记录不存在"，返回列表刷新 |
| 409 | 资源存在冲突（如删除时有客源 / 牵线记录，或绑定冲突） | `{"detail":"<冲突原因>"}` | 阻断操作，提示用户先处理关联数据 |
| 500 | 未预期的服务端异常 | `{"detail":"服务器内部错误"}` | 提示"稍后重试"，必要时上报 |

---

## 二、接口详细契约

### 2.1 `GET /admin/matchmakers` — 红娘列表

**基本信息**
- 用途：分页查询红娘列表，支持按关键词、门店、分成级别、锁定状态筛选；页面首次加载与搜索时调用。  
- URL：`GET /api/v1/admin/matchmakers`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（query）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | `≥ 1` | 页码 |
| `page_size` | query | int | 否 | 20 | `1 ~ 100` | 每页条数 |
| `keyword` | query | string | 否 | 空 | `≤ 100` 字符 | 模糊匹配红娘昵称 / 后台账号 / 手机号 |
| `store_id` | query | int | 否 | — | `≥ 1` | 按门店 ID 过滤 |
| `commission_level_id` | query | int | 否 | — | `≥ 1` | 按分成级别 ID 过滤 |
| `locked` | query | bool | 否 | — | `true` / `false` | 按锁定状态过滤（不传表示不过滤） |

**请求体示例**

```
GET /api/v1/admin/matchmakers?page=1&page_size=20&keyword=%E5%B0%8F%E7%BE%8E&store_id=1&commission_level_id=2&locked=false
Authorization: Bearer <access-token>
```

无请求体。  
非法示例：`page=0`（`< 1` → 422）、`page_size=200`（`> 100` → 422）、`keyword=` 长度 > 100（→ 422）、`locked=1`（非布尔 → 422）。

**返回参数（分页）**

| 字段 | 类型 | 必返 | 空值含义 | 枚举 / 含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | — | 红娘行数组 | — |
| `items[].id` | int | 是 | — | — | 红娘档案主键 | `12` |
| `items[].avatar` | string? | 是 | `null` | — | 头像 URL | `"https://cdn.example.com/a.png"` |
| `items[].display_name` | string | 是 | — | — | 展示名（昵称） | `"小美"` |
| `items[].username` | string | 是 | — | — | 红娘后台登录账号 | `"matchmaker_xiaomei"` |
| `items[].store_id` | int? | 是 | `null` | — | 所属门店 ID | `1` |
| `items[].store_name` | string? | 是 | `null` | — | 所属门店名 | `"总店"` |
| `items[].role_tag` | string | 是 | — | `"super"` / `"normal"` | 角色标识：超级 / 普通 | `"normal"` |
| `items[].role_label` | string | 是 | — | — | 角色展示名 | `"普通红娘"` |
| `items[].phone` | string | 是 | — | — | 手机号（原样返回，前端按需掩码） | `"13800000000"` |
| `items[].wechat` | string? | 是 | `null` | — | 微信号 | `"xiaomei_wx"` |
| `items[].commission_level_id` | int? | 是 | `null` | — | 分成级别 ID | `2` |
| `items[].commission_level_name` | string? | 是 | `null` | — | 分成级别名 | `"中级分成"` |
| `items[].commission_rate` | string(decimal) | 是 | `"0.0000"` | — | 分成比例（%），字符串 | `"15.0000"` |
| `items[].success_count` | int | 是 | `0` | — | 牵线成功次数 | `30` |
| `items[].commission_amount` | string(decimal) | 是 | `"0.00"` | — | 累计分成金额（元） | `"4500.00"` |
| `items[].locked` | bool | 是 | — | — | 是否锁定 | `false` |
| `items[].visible` | bool | 是 | — | — | 前台是否展示 | `true` |
| `items[].description` | string? | 是 | `null` | — | 个人简介 | `"..."` |
| `items[].slogan` | string? | 是 | `null` | — | 标语 | `"为你牵线"` |
| `items[].sort` | int | 是 | `0` | — | 排序值，越小越靠前 | `0` |
| `items[].contact_editable` | bool | 是 | — | — | 联系方式是否允许红娘自己改 | `true` |
| `items[].lock_at` | datetime? | 是 | `null` | — | 最近锁定时间 | `"2026-09-01T10:00:00"` |
| `items[].created_at` / `items[].updated_at` | datetime? | 是 | `null` | — | 创建 / 更新时间 | `"2026-08-01T10:00:00"` |
| `page` / `page_size` / `total` / `has_more` | int / bool | 是 | — | — | 分页元信息 | `page=1,page_size=20,total=35,has_more=true` |

**返回示例（有数据）**

```json
{
  "items": [
    {
      "id": 12,
      "avatar": "https://cdn.example.com/a.png",
      "display_name": "小美",
      "username": "matchmaker_xiaomei",
      "store_id": 1,
      "store_name": "总店",
      "role_tag": "normal",
      "role_label": "普通红娘",
      "phone": "13800000000",
      "wechat": "xiaomei_wx",
      "commission_level_id": 2,
      "commission_level_name": "中级分成",
      "commission_rate": "15.0000",
      "success_count": 30,
      "commission_amount": "4500.00",
      "locked": false,
      "visible": true,
      "description": "资深牵线红娘",
      "slogan": "为你牵线",
      "sort": 0,
      "contact_editable": true,
      "lock_at": null,
      "created_at": "2026-08-01T10:00:00",
      "updated_at": "2026-09-08T15:00:00"
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
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：页面加载先调用本接口取首屏列表；筛选条件变化后重新调用（前端自行维护 `page` / `page_size`）。  
- 幂等：查询接口天然幂等，重复调用返回相同结果。  
- 限流：无特殊限制；前端搜索建议做 300ms 防抖。  
- 状态流转：`locked` 由 `PATCH .../lock` 切换；`visible` 由 `PATCH .../visibility` 切换（见 2.8 / 2.9）。  
- 边界场景：无匹配返回空数组（`total=0`）；`keyword` 同时匹配昵称 / 账号 / 手机三种字段。

**错误**：见「通用约定 - 通用错误」（401 / 403 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（分页接口含分页字段说明；含空数据示例）
- [x] 有"使用方法与业务规则"小节，覆盖前置条件、调用顺序、幂等、限流、状态流转和边界场景
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.2 `GET /admin/matchmakers/user-candidates` — 搜索可绑定用户

**基本信息**
- 用途：新增红娘时，按关键词搜索「可绑定」的平台用户（用于选择 `user_id` 绑定）。  
- URL：`GET /api/v1/admin/matchmakers/user-candidates`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（query）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `keyword` | query | string | **是** | — | `2 ~ 100` 字符 | 用户昵称 / 手机精确 / 模糊关键词，用于匹配候选用户 |

**请求体示例**

```
GET /api/v1/admin/matchmakers/user-candidates?keyword=%E5%B0%8F%E7%BE%8E
Authorization: Bearer <access-token>
```

无请求体。  
非法示例：`keyword` 缺省（→ 422）、`keyword=a`（长度 < 2 → 422）。

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | 候选用户数组 | — |
| `items[].id` | int | 是 | — | 用户 `users.id`（可绑定为目标 `user_id`） | `1001` |
| `items[].nickname` | string | 是 | — | 用户昵称 | `"小美"` |
| `items[].phone` | string? | 是 | `null` | 手机号（前端按需掩码） | `"13800000000"` |
| `items[].avatar` | string? | 是 | `null` | 头像 URL | `null` |

**返回示例（有数据）**

```json
{
  "items": [
    { "id": 1001, "nickname": "小美", "phone": "13800000000", "avatar": null }
  ]
}
```

**返回示例（空数据）**

```json
{ "items": [] }
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`keyword` 必填且长度 ≥ 2。  
- 调用顺序：在「新增红娘」表单的"绑定用户"步骤先调用本接口取得候选，选中后将 `id` 作为 `user_id` 提交 `POST /admin/matchmakers`。  
- 幂等：查询天然幂等。  
- 边界场景：查不到用户返回空数组；已绑定红娘的用户是否出现在候选中由后端决定（已绑定用户通常不应再被选，详见 2.3 绑定语义）。

**错误**：见通用错误（401 / 403 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节，覆盖前置条件、调用顺序、幂等、限流、状态流转和边界场景
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.3 `POST /admin/matchmakers` — 新增红娘

**基本信息**
- 用途：新增一名红娘；支持「直接绑定已有用户」「按昵称/手机查用户后绑定」「用手机号新建用户」三种语义。  
- URL：`POST /api/v1/admin/matchmakers`  
- Method：POST  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`application/json`  
- 成功：200（返回新建红娘详情，结构同 2.6 详情）  

**请求参数（body）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | body | int | 否 | — | `≥ 1` | 直接绑定到指定平台用户 ID（与 `lookup` 二选一或都不传） |
| `lookup` | body | string | 否 | — | `1 ~ 100` 字符 | 按昵称 / 手机精确查找用户的关键词（与 `user_id` 二选一） |
| `lookup_by` | body | string | 否 | `"nickname"` | enum `nickname` / `phone` | `lookup` 的查找字段 |
| `avatar` | body | string | 否 | — | `≤ 500` | 头像 URL |
| `display_name` | body | string | **是** | — | `1 ~ 32` | 红娘展示名（昵称） |
| `phone` | body | string | **是** | — | `11 ~ 20` 位纯数字 | 红娘手机号（也用于"无绑定用户时新建用户"） |
| `wechat` | body | string | 否 | — | `≤ 64` | 微信号 |
| `store_id` | body | int | 否 | — | `≥ 1` | 所属门店 ID |
| `commission_level_id` | body | int | 否 | — | `≥ 1` | 分成级别 ID |
| `role_tag` | body | string | 否 | `"normal"` | enum `super` / `normal` | 角色标识 |
| `description` | body | string | 否 | — | `≤ 2000` | 个人简介 |
| `visible` | body | bool | 否 | `true` | — | 前台是否展示 |
| `slogan` | body | string | 否 | — | `≤ 64` | 标语 |
| `sort` | body | int | 否 | `0` | `≥ 0` | 排序值 |
| `contact_editable` | body | bool | 否 | `true` | — | 联系方式是否允许红娘自改 |
| `lock_at` | body | datetime? | 否 | `null` | datetime 或 `null` | 预置锁定时间（一般留空） |

**请求体示例（直接绑定已有用户）**

```json
POST /api/v1/admin/matchmakers
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "user_id": 1001,
  "display_name": "小美",
  "phone": "13800000000",
  "store_id": 1,
  "commission_level_id": 2,
  "role_tag": "normal",
  "visible": true,
  "slogan": "为你牵线",
  "sort": 0,
  "contact_editable": true
}
```

**请求体示例（按昵称查找后绑定）**

```json
{
  "lookup": "小美",
  "lookup_by": "nickname",
  "display_name": "小美",
  "phone": "13800000000",
  "store_id": 1
}
```

**请求体示例（仅用手机号新建用户）**

```json
{
  "display_name": "小美",
  "phone": "13800000000"
}
```

非法示例：
- 缺 `display_name` → 422（`display_name` 必填）。
- 缺 `phone` → 422（`phone` 必填）。
- `phone` 含字母（如 `"138abc00000"`）→ 422（纯数字校验）。
- `display_name` 长度 33 → 422。
- `lookup` 与 `user_id` 同时传 → 422（互斥依赖，后端按"直接绑定优先"或报错，以 422 处理冲突）。

**绑定语义（务必理解）**
- 传 `user_id`：直接绑定到该用户。  
- 仅传 `lookup` + `lookup_by`：按 `nickname` / `phone` 精确查 `users WHERE status=1`，取最新一条绑定。  
- 两者都不传：用 `phone` **新建**平台用户再绑定。  
- 手机号已存在 或 该用户已绑定红娘 → **409**。  
- `lookup` 查不到 → **404**。

**返回参数**：同 2.6「红娘详情」字段（`id, avatar, ..., account_id, data_scope, intro`）。

**返回示例**（节选，完整字段见 2.6）

```json
{
  "id": 12,
  "display_name": "小美",
  "username": "matchmaker_xiaomei",
  "phone": "13800000000",
  "role_tag": "normal",
  "locked": false,
  "visible": true,
  "account_id": 1001,
  "data_scope": "STORE",
  "intro": null,
  "created_at": "2026-09-08T15:00:00",
  "updated_at": "2026-09-08T15:00:00"
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`display_name` / `phone` 必填且合法。  
- 调用顺序：建议先用 2.2 `user-candidates` 检索候选，确认是否已有可绑定用户，再构造本请求。  
- 幂等：非幂等（重复提交会新建重复红娘或触发 409）。前端提交按钮需防重复点击。  
- 状态流转：新建后 `locked=false, visible=true`（缺省）；后续由 2.8 / 2.9 切换。  
- 边界场景：见「绑定语义」的 409 / 404 规则。

**错误**

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 409 | 手机号已存在 / 该用户已绑定红娘 | `{"detail":"该手机号已存在或该用户已绑定红娘"}` | 提示"已存在"，引导复用现有红娘 |
| 404 | `lookup` 按昵称 / 手机查不到用户 | `{"detail":"未找到匹配的用户"}` | 提示"查无此用户"，改为直接填 `user_id` 或新建 |
| 422 | 必填缺失 / 格式 / 范围 / 互斥校验失败 | `{"detail":"参数校验失败：<字段>"}` | 表单回填 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含 3 种合法 + 多个非法示例）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节，覆盖前置条件、调用顺序、幂等、限流、状态流转和边界场景
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.4 `GET /admin/matchmakers/tutorial` — 红娘使用教程

**基本信息**
- 用途：获取红娘后台使用教程内容（图文 / 步骤），用于前台红娘端引导。  
- URL：`GET /api/v1/admin/matchmakers/tutorial`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数**：无。

**请求体示例**

```
GET /api/v1/admin/matchmakers/tutorial
Authorization: Bearer <access-token>
```

无请求体。非法示例：无请求体相关非法场景（GET 无 body）。

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `content` | string? | 是 | `null` 或 `""` | 教程正文（Markdown / HTML 片段） |
| `updated_at` | datetime? | 是 | `null` | 教程最后更新时间 |

> 注：具体字段以实际返回为准；若返回为单对象则按上表逐字段解释，若为空返回 `{}` 或 `{content:""}`。

**返回示例（有数据）**

```json
{
  "content": "## 红娘使用教程\n1. 登录后台\n2. 接待客源\n3. 发起牵线",
  "updated_at": "2026-09-01T09:00:00"
}
```

**返回示例（空数据）**

```json
{ "content": "", "updated_at": null }
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：红娘端"教程"入口点击后调用；可缓存。  
- 幂等：查询天然幂等。

**错误**：见通用错误（401 / 403）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义（无参数已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例（无 body，GET 无非法参数场景）

---

### 2.5 `GET /admin/matchmakers/{matchmaker_id}` — 红娘详情

**基本信息**
- 用途：获取单个红娘完整档案（含列表无的 `account_id` / `data_scope` / `intro`）。  
- URL：`GET /api/v1/admin/matchmakers/{matchmaker_id}`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（path）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `matchmaker_id` | path | int | **是** | — | `≥ 1` | 红娘档案主键 |

**请求体示例**

```
GET /api/v1/admin/matchmakers/12
Authorization: Bearer <access-token>
```

无请求体。非法示例：`matchmaker_id=0` 或非整数（→ 422 / 404）。

**返回参数**（在列表字段基础上额外 3 个）

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| 列表字段（见 2.1） | — | 是 | 同列表 `items[].*` 全部字段 |
| `account_id` | int? | 是 | 绑定的平台用户 `users.id`（未绑定时 `null`） |
| `data_scope` | string | 是 | 数据权限范围：`SELF` / `STORE` / `ORGANIZATION` / `ALL` |
| `intro` | string? | 是 | 红娘自我介绍详情（区别于列表的 `description` 摘要） |

**返回示例**

```json
{
  "id": 12,
  "avatar": "https://cdn.example.com/a.png",
  "display_name": "小美",
  "username": "matchmaker_xiaomei",
  "store_id": 1,
  "store_name": "总店",
  "role_tag": "normal",
  "role_label": "普通红娘",
  "phone": "13800000000",
  "wechat": "xiaomei_wx",
  "commission_level_id": 2,
  "commission_level_name": "中级分成",
  "commission_rate": "15.0000",
  "success_count": 30,
  "commission_amount": "4500.00",
  "locked": false,
  "visible": true,
  "description": "资深牵线红娘",
  "slogan": "为你牵线",
  "sort": 0,
  "contact_editable": true,
  "lock_at": null,
  "created_at": "2026-08-01T10:00:00",
  "updated_at": "2026-09-08T15:00:00",
  "account_id": 1001,
  "data_scope": "STORE",
  "intro": "专注婚恋牵线 5 年"
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：列表点击某行进入详情页时调用；编辑页先取详情回填。  
- 幂等：查询天然幂等。

**错误**

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `matchmaker_id` 不存在 | `{"detail":"红娘不存在"}` | 提示"记录不存在"，返回列表 |
| 422 | `matchmaker_id` 非整数 / `< 1` | `{"detail":"参数校验失败"}` | 校验 URL |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.6 `PUT /admin/matchmakers/{matchmaker_id}` — 编辑红娘

**基本信息**
- 用途：编辑红娘档案，所有字段可选；可改登录密码。  
- URL：`PUT /api/v1/admin/matchmakers/{matchmaker_id}`  
- Method：PUT  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`application/json`  
- 成功：200（返回更新后详情，结构同 2.5）  

**请求参数（path）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `matchmaker_id` | path | int | **是** | — | `≥ 1` | 红娘档案主键 |

**请求参数（body，全部可选）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | body | int | 否 | — | `≥ 1` | 改绑用户 ID（谨慎使用） |
| `lookup` | body | string | 否 | — | `1 ~ 100` | 按昵称/手机查用户后改绑 |
| `lookup_by` | body | string | 否 | `"nickname"` | enum `nickname`/`phone` | 查找字段 |
| `avatar` | body | string | 否 | — | `≤ 500` | 头像 URL |
| `display_name` | body | string | 否 | — | `1 ~ 32` | 展示名 |
| `phone` | body | string | 否 | — | `11 ~ 20` 位纯数字 | 手机号 |
| `password` | body | string | 否 | — | `8 ~ 128` | 重置后台登录密码（**新增字段**，仅编辑接口支持） |
| `wechat` | body | string | 否 | — | `≤ 64` | 微信号 |
| `store_id` | body | int | 否 | — | `≥ 1` | 门店 ID |
| `commission_level_id` | body | int | 否 | — | `≥ 1` | 分成级别 ID |
| `role_tag` | body | string | 否 | — | enum `super`/`normal` | 角色 |
| `description` | body | string | 否 | — | `≤ 2000` | 简介 |
| `visible` | body | bool | 否 | — | — | 前台展示 |
| `slogan` | body | string | 否 | — | `≤ 64` | 标语 |
| `sort` | body | int | 否 | — | `≥ 0` | 排序 |
| `contact_editable` | body | bool | 否 | — | — | 联系方式可自改 |
| `lock_at` | body | datetime? | 否 | — | datetime / `null` | 锁定时间 |

**请求体示例**

```json
PUT /api/v1/admin/matchmakers/12
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "display_name": "小美（改名）",
  "phone": "13800000001",
  "password": "<new-password-placeholder>",
  "commission_level_id": 3,
  "visible": false
}
```

非法示例：
- `password` 长度 7（`< 8` → 422）。
- `phone` 非纯数字（→ 422）。
- `display_name` 长度 > 32（→ 422）。
- 请求体为空 `{}` → 422（至少需传一个字段，后端要求至少一个可更新字段）。

**返回参数**：同 2.5 详情。

**返回示例**：见 2.5 示例（返回更新后完整详情）。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在；至少传一个可更新字段。  
- 调用顺序：编辑页 `GET 详情` 回填 → 用户修改 → `PUT` 提交。  
- 幂等：`PUT` 为全量可选更新，重复提交相同内容结果一致；但连续不同提交以最后一次为准。  
- 边界场景：改 `phone` 若与已存在用户冲突 → 409（同 2.3 绑定语义）。

**错误**

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `matchmaker_id` 不存在 | `{"detail":"红娘不存在"}` | 提示返回列表 |
| 409 | 改绑手机号/用户冲突 | `{"detail":"该手机号/用户已存在"}` | 表单回填 |
| 422 | 字段校验 / 空 body | `{"detail":"参数校验失败"}` | 表单回填 |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.7 `DELETE /admin/matchmakers/{matchmaker_id}` — 删除红娘

**基本信息**
- 用途：删除（解绑）一名红娘档案。  
- URL：`DELETE /api/v1/admin/matchmakers/{matchmaker_id}`  
- Method：DELETE  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200（或 204，返回 `{"detail":"删除成功"}` 风格）  

**请求参数（path）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `matchmaker_id` | path | int | **是** | — | `≥ 1` | 红娘档案主键 |

**请求体示例**

```
DELETE /api/v1/admin/matchmakers/12
Authorization: Bearer <access-token>
```

无请求体。非法示例：`matchmaker_id` 非整数（→ 422 / 404）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `detail` | string | 是 | 操作结果描述，如 `"删除成功"` |

**返回示例**

```json
{ "detail": "删除成功" }
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：列表 / 详情页点删除 → 二次确认弹窗 → 调本接口。  
- 幂等：删除为破坏性操作，重复删除第二次会 404（已不存在）。  
- 状态流转 / 边界场景：**有客源或牵线记录时禁止删除，返回 409**；需先清理关联数据或转交后删除。

**错误**

| HTTP | 触发条件 | 错误响应 JSON | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `matchmaker_id` 不存在 | `{"detail":"红娘不存在"}` | 提示返回列表 |
| 409 | 存在客源 / 牵线记录 | `{"detail":"该红娘存在关联客源或牵线记录，无法删除"}` | 提示"请先处理关联数据" |

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.8 `PATCH /admin/matchmakers/{matchmaker_id}/lock` — 锁定 / 解锁

**基本信息**
- 用途：切换红娘锁定状态（锁定后禁止登录 / 操作）。  
- URL：`PATCH /api/v1/admin/matchmakers/{matchmaker_id}/lock`  
- Method：PATCH  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`application/json`  
- 成功：200（返回更新后详情，结构同 2.5）  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求参数（body）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `locked` | body | bool | **是** | — | `true` / `false` | `true`=锁定，`false`=解锁 |

**请求体示例**

```json
PATCH /api/v1/admin/matchmakers/12/lock
Authorization: Bearer <access-token>
Content-Type: application/json

{ "locked": true }
```

非法示例：`{"locked": 1}`（非布尔 → 422）、缺 `locked`（→ 422）。

**返回参数**：同 2.5 详情（`locked` 字段反映新状态，`lock_at` 为最近锁定时间）。

**返回示例**：见 2.5（其中 `"locked": true, "lock_at": "2026-09-08T15:00:00"`）。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：列表行内"锁定/解锁"开关直接触发。  
- 幂等：重复提交相同 `locked` 值结果一致（状态不变）。  
- 边界场景：锁定后该红娘后台 Token 应失效（由鉴权层控制）。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.9 `PATCH /admin/matchmakers/{matchmaker_id}/visibility` — 前台展示开关

**基本信息**
- 用途：切换红娘是否在前台（用户端）展示。  
- URL：`PATCH /api/v1/admin/matchmakers/{matchmaker_id}/visibility`  
- Method：PATCH  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`application/json`  
- 成功：200（返回更新后详情，结构同 2.5）  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求参数（body）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `visible` | body | bool | **是** | — | `true` / `false` | `true`=前台展示，`false`=隐藏 |

**请求体示例**

```json
PATCH /api/v1/admin/matchmakers/12/visibility
Authorization: Bearer <access-token>
Content-Type: application/json

{ "visible": false }
```

非法示例：缺 `visible`（→ 422）、`{"visible": "yes"}`（非布尔 → 422）。

**返回参数**：同 2.5 详情（`visible` 反映新状态）。

**返回示例**：见 2.5（其中 `"visible": false`）。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：列表行内"展示/隐藏"开关触发。  
- 幂等：重复提交相同值结果一致。  
- 边界场景：`visible=false` 时前台红娘列表不展示该红娘，但不影响其历史分成 / 牵线数据。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.10 `GET /admin/menus/tree` — 后台菜单树

**基本信息**
- 用途：获取红娘后台菜单树（用于权限分配 UI 与左侧导航）。  
- URL：`GET /api/v1/admin/menus/tree`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数**：无。

**请求体示例**

```
GET /api/v1/admin/menus/tree
Authorization: Bearer <access-token>
```

无请求体。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items` / 根数组 | array | 是 | 菜单节点数组（树形） |
| `items[].id` | int | 是 | 菜单节点 ID |
| `items[].name` | string | 是 | 菜单名 |
| `items[].code` | string? | 是 | 菜单编码（权限标识） |
| `items[].parent_id` | int? | 是 | 父节点 ID（`null`=根） |
| `items[].children` | array? | 是 | 子节点数组（同结构，递归展开） |

**返回示例**

```json
[
  {
    "id": 1,
    "name": "红娘管理",
    "code": "matchmaker",
    "parent_id": null,
    "children": [
      { "id": 11, "name": "红娘列表", "code": "matchmaker.manage", "parent_id": 1, "children": [] }
    ]
  }
]
```

**返回示例（空数据）**

```json
[]
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：进入"菜单权限"配置页时调用，构建树形勾选 UI。  
- 幂等：查询天然幂等。

**错误**：见通用错误（401 / 403）。

**文档完成自检清单**
- [x] 有请求参数表（无参数已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例（GET 无 body 非法场景）

---

### 2.11 `GET /admin/matchmakers/{matchmaker_id}/permissions` — 查菜单权限

**基本信息**
- 用途：查询某红娘被授予的菜单权限（菜单 ID 列表）。  
- URL：`GET /api/v1/admin/matchmakers/{matchmaker_id}/permissions`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求体示例**

```
GET /api/v1/admin/matchmakers/12/permissions
Authorization: Bearer <access-token>
```

无请求体。非法示例：`matchmaker_id` 非整数（→ 422 / 404）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_id` | int | 是 | 红娘 ID（**驼峰**字段名） |
| `menuIds` | int[] | 是 | 已授权菜单节点 ID 数组（**驼峰**字段名） |

**返回示例（有数据）**

```json
{ "matchmaker_id": 12, "menuIds": [1, 11, 12, 20] }
```

**返回示例（空数据）**

```json
{ "matchmaker_id": 12, "menuIds": [] }
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：进入"菜单权限"编辑页时先 `GET` 取当前 `menuIds` 回填勾选框，再 `PUT` 保存。  
- 幂等：查询天然幂等。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.12 `PUT /admin/matchmakers/{matchmaker_id}/permissions` — 存菜单权限

**基本信息**
- 用途：保存某红娘的菜单权限（全量覆盖 `menuIds`）。  
- URL：`PUT /api/v1/admin/matchmakers/{matchmaker_id}/permissions`  
- Method：PUT  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`application/json`  
- 成功：200（返回保存后的权限，结构同 2.11）  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求参数（body）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `menuIds` | body | int[] | **是** | — | 数组长度 `≤ 200`；元素为菜单节点 ID | 该红娘被授予的菜单 ID 集合（全量覆盖） |

**请求体示例**

```json
PUT /api/v1/admin/matchmakers/12/permissions
Authorization: Bearer <access-token>
Content-Type: application/json

{ "menuIds": [1, 11, 12, 20] }
```

非法示例：
- 缺 `menuIds`（→ 422）。
- `menuIds` 长度 201（→ 422，超过 200 上限）。
- `menuIds` 含非整数（→ 422）。

**返回参数**：同 2.11（`matchmaker_id` + `menuIds`）。

**返回示例**：同 2.11 有数据示例。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：`GET 2.11` 回填 → 用户勾选 → `PUT` 全量提交（覆盖式，非增量）。  
- 幂等：全量覆盖，重复提交相同 `menuIds` 结果一致。  
- 边界场景：提交空数组 `[]` 表示清空该红娘所有菜单权限（谨慎）。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.13 `GET /admin/matchmakers/{matchmaker_id}/work-report` — 工作汇报

**基本信息**
- 用途：获取某红娘在时间段内的工作汇总指标（客源 / 牵线 / 成功 / 跟进等）。  
- URL：`GET /api/v1/admin/matchmakers/{matchmaker_id}/work-report`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求参数（query）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `from` | query | string | 否 | 最近 30 天前 | `YYYY-MM-DD` | 统计开始日期（含） |
| `to` | query | string | 否 | 今天 | `YYYY-MM-DD` | 统计结束日期（含） |

> 约束：`to - from` 跨度 **> 366 天** → 422。

**请求体示例**

```
GET /api/v1/admin/matchmakers/12/work-report?from=2026-08-01&to=2026-08-31
Authorization: Bearer <access-token>
```

无请求体。非法示例：`from=2026-08-01&to=2024-08-01`（跨度 > 366 天 → 422）、`from=2026/08/01`（格式错误 → 422）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_id` | int | 是 | 红娘 ID |
| `from_date` | string | 是 | 实际统计开始日期 `YYYY-MM-DD` |
| `to_date` | string | 是 | 实际统计结束日期 `YYYY-MM-DD` |
| `new_lead_count` | int | 是 | 新增客源数 |
| `new_member_count` | int | 是 | 新增会员数 |
| `lead_follow_up_count` | int | 是 | 客源跟进次数 |
| `matchmaking_count` | int | 是 | 牵线次数 |
| `success_count` | int | 是 | 牵线成功次数 |
| `follow_up_count` | int | 是 | 跟进总次数 |
| `meeting_request_count` | int | 是 | 约见申请数 |
| `meeting_arranged_count` | int | 是 | 已安排约见数 |
| `commission_amount` | string(decimal) | 是 | 期间分成金额（元，字符串） |
| `offline_income` | string(decimal) | 是 | 期间线下收入（元，字符串） |
| `assigned_member_count` | int | 是 | 被分配会员数 |

**返回示例**

```json
{
  "matchmaker_id": 12,
  "from_date": "2026-08-01",
  "to_date": "2026-08-31",
  "new_lead_count": 20,
  "new_member_count": 8,
  "lead_follow_up_count": 35,
  "matchmaking_count": 12,
  "success_count": 5,
  "follow_up_count": 40,
  "meeting_request_count": 6,
  "meeting_arranged_count": 4,
  "commission_amount": "1200.00",
  "offline_income": "3000.00",
  "assigned_member_count": 15
}
```

**返回示例（空数据 / 区间无记录）**

```json
{
  "matchmaker_id": 12,
  "from_date": "2026-08-01",
  "to_date": "2026-08-31",
  "new_lead_count": 0,
  "new_member_count": 0,
  "lead_follow_up_count": 0,
  "matchmaking_count": 0,
  "success_count": 0,
  "follow_up_count": 0,
  "meeting_request_count": 0,
  "meeting_arranged_count": 0,
  "commission_amount": "0.00",
  "offline_income": "0.00",
  "assigned_member_count": 0
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：报表页选择区间 → 调用本接口取汇总卡；详情漏斗见 2.14。  
- 幂等：查询天然幂等。  
- 限流：无特殊限制；前端区间选择建议限制最大 366 天。  
- 边界场景：区间无数据返回全 0 计数与 `"0.00"` 金额（非 `null`）。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.14 `GET /admin/matchmakers/{matchmaker_id}/report` — 详情报表

**基本信息**
- 用途：获取某红娘时间段内的详情报表（在 2.13 工作汇报基础上增加漏斗、趋势、成功率、导出链接）。  
- URL：`GET /api/v1/admin/matchmakers/{matchmaker_id}/report`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体  
- 成功：200  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。  
**请求参数（query）**：同 2.13（`from` / `to`，跨度 > 366 天 → 422）。

**请求体示例**

```
GET /api/v1/admin/matchmakers/12/report?from=2026-08-01&to=2026-08-31
Authorization: Bearer <access-token>
```

无请求体。非法示例：同 2.13。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_id` | int | 是 | 红娘 ID |
| `from_date` / `to_date` | string | 是 | 统计区间 |
| `work_report` | object | 是 | 同 2.13 工作汇报对象（字段完全一致） |
| `work_report.new_lead_count` ... | — | 是 | （嵌套，结构同 2.13 返回参数） |
| `funnel` | array | 是 | 转化漏斗数组 |
| `funnel[].stage` | string | 是 | 漏斗阶段标识 |
| `funnel[].label` | string | 是 | 漏斗阶段展示名 |
| `funnel[].count` | int | 是 | 该阶段数量 |
| `monthly_trends` | array | 是 | **当前为占位值：固定返回空数组 `[]`** |
| `success_rate` | string | 是 | **当前为占位值：固定返回 `"0.00"`** |
| `platform_success_rate` | string | 是 | **当前为占位值：固定返回 `"0.00"`** |
| `export_url` | string? | 是 | **当前为占位值：固定返回 `null`**（导出功能待二期） |

> ⚠️ 如实标注：`monthly_trends`、`success_rate`、`platform_success_rate`、`export_url` 四个字段**当前为占位实现**，并非真实统计值。前端不应依赖其真实业务含义展示，待后端二期补全。

**返回示例（含占位字段）**

```json
{
  "matchmaker_id": 12,
  "from_date": "2026-08-01",
  "to_date": "2026-08-31",
  "work_report": {
    "new_lead_count": 20,
    "new_member_count": 8,
    "lead_follow_up_count": 35,
    "matchmaking_count": 12,
    "success_count": 5,
    "follow_up_count": 40,
    "meeting_request_count": 6,
    "meeting_arranged_count": 4,
    "commission_amount": "1200.00",
    "offline_income": "3000.00",
    "assigned_member_count": 15
  },
  "funnel": [
    { "stage": "lead", "label": "客源", "count": 20 },
    { "stage": "matchmaking", "label": "牵线", "count": 12 },
    { "stage": "success", "label": "成功", "count": 5 }
  ],
  "monthly_trends": [],
  "success_rate": "0.00",
  "platform_success_rate": "0.00",
  "export_url": null
}
```

**返回示例（空数据）**

```json
{
  "matchmaker_id": 12,
  "from_date": "2026-08-01",
  "to_date": "2026-08-31",
  "work_report": {
    "new_lead_count": 0, "new_member_count": 0, "lead_follow_up_count": 0,
    "matchmaking_count": 0, "success_count": 0, "follow_up_count": 0,
    "meeting_request_count": 0, "meeting_arranged_count": 0,
    "commission_amount": "0.00", "offline_income": "0.00", "assigned_member_count": 0
  },
  "funnel": [],
  "monthly_trends": [],
  "success_rate": "0.00",
  "platform_success_rate": "0.00",
  "export_url": null
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。  
- 调用顺序：报表详情页先取本接口；`work_report` 与 2.13 同源，可只调本接口以省一次请求。  
- 幂等：查询天然幂等。  
- 边界场景：`funnel` / `monthly_trends` 可能为空数组；`export_url` 为 `null` 时前端隐藏"导出"按钮。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [x] 有成功返回示例（含空数据示例与占位字段标注）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.15 `POST /admin/matchmakers/{matchmaker_id}/poster` — 生成海报

**基本信息**
- 用途：为某红娘生成专属推广海报（含二维码）。  
- URL：`POST /api/v1/admin/matchmakers/{matchmaker_id}/poster`  
- Method：POST  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体（POST 但无 body）  
- 成功：200  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求体示例**

```
POST /api/v1/admin/matchmakers/12/poster
Authorization: Bearer <access-token>
```

无请求体。非法示例：`matchmaker_id` 非整数（→ 422 / 404）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_id` | int | 是 | 红娘 ID |
| `url` | string | 是 | 海报图片 URL |
| `qr_content` | string | 是 | 海报二维码内容（如推广落地页链接 / 参数） |

**返回示例**

```json
{
  "matchmaker_id": 12,
  "url": "https://cdn.example.com/poster/12.png",
  "qr_content": "https://xuanshiai.com/?m=12"
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：详情页"生成海报"按钮触发；返回后前端展示 / 提供下载。  
- 幂等：每次调用都会重新生成（非幂等，可能覆盖旧海报）；前端避免连点。  
- 边界场景：`matchmaker_id` 不存在 → 404。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.16 `POST /admin/matchmakers/{matchmaker_id}/platform-token` — 红娘平台免登

**基本信息**
- 用途：为某红娘生成平台侧免登 Token（后台代红娘登录平台用的 `access_token` / `refresh_token` + 跳转地址）。  
- URL：`POST /api/v1/admin/matchmakers/{matchmaker_id}/platform-token`  
- Method：POST  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：无请求体（POST 但无 body）  
- 成功：200  

**请求参数（path）**：`matchmaker_id`（int，`≥ 1`，必填）。

**请求体示例**

```
POST /api/v1/admin/matchmakers/12/platform-token
Authorization: Bearer <access-token>
```

无请求体。非法示例：`matchmaker_id` 非整数（→ 422 / 404）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmaker_id` | int | 是 | 红娘 ID |
| `access_token` | string | 是 | 平台侧访问 Token（占位，无真实密钥） |
| `refresh_token` | string | 是 | 平台侧刷新 Token（占位，无真实密钥） |
| `token_type` | string | 是 | 固定 `"bearer"` |
| `expires_in` | int | 是 | Access Token 有效秒数 |
| `jump_url` | string | 是 | 免登跳转地址（携带 Token 参数） |

**返回示例**

```json
{
  "matchmaker_id": 12,
  "access_token": "<platform-access-token>",
  "refresh_token": "<platform-refresh-token>",
  "token_type": "bearer",
  "expires_in": 7200,
  "jump_url": "https://xuanshiai.com/sso?t=<platform-access-token>"
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`matchmaker_id` 存在。  
- 调用顺序：后台"代红娘登录平台"按钮触发；拿到 `jump_url` 后前端 `window.open` 跳转。  
- 幂等：每次调用生成新 Token（旧 Token 可能失效）；非幂等。  
- 边界场景：Token 含敏感信息，前端仅用于跳转，不得落库 / 打印日志。

**错误**：见通用错误（401 / 403 / 404 / 422）。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（无请求体时已明确标注，Token 用占位符）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

### 2.17 `GET /admin/dict/commission-levels` — 分成级别字典

**基本信息**
- 用途：取分成级别字典（下拉选项用）。  
- URL：`GET /api/v1/admin/dict/commission-levels`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.read`  
- Content-Type：无请求体  
- 成功：200  

**请求参数**：无。

**请求体示例**

```
GET /api/v1/admin/dict/commission-levels
Authorization: Bearer <access-token>
```

无请求体。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `[]` 根数组 | array | 是 | 分成级别数组 |
| `id` | int | 是 | 级别 ID |
| `code` | string | 是 | 业务编码（junior/intermediate/senior/partner） |
| `name` | string | 是 | 级别名 |
| `rate_percent` | string(decimal) | 是 | 默认分成比例（%），字符串 |
| `sort` | int | 是 | 排序 |
| `status` | int | 是 | `1` 启用 / `2` 停用 |

**返回示例**

```json
[
  { "id": 1, "code": "junior", "name": "初级分成", "rate_percent": "10.0000", "sort": 1, "status": 1 },
  { "id": 2, "code": "intermediate", "name": "中级分成", "rate_percent": "15.0000", "sort": 2, "status": 1 }
]
```

**返回示例（空数据）**

```json
[]
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。  
- 调用顺序：新增 / 编辑红娘表单的"分成级别"下拉在打开时调用。  
- 幂等：查询天然幂等。

**错误**：见通用错误（401 / 403）。

**文档完成自检清单**
- [x] 有请求参数表（无参数已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例（GET 无 body 非法场景）

---

### 2.18 `GET /admin/dict/stores` — 门店字典

**基本信息**
- 用途：取门店字典（下拉选项用）。  
- URL：`GET /api/v1/admin/dict/stores`  
- Method：GET  
- 登录：必需  
- 权限：`matchmaker.read`  
- Content-Type：无请求体  
- 成功：200  

**请求参数**：无。

**请求体示例**

```
GET /api/v1/admin/dict/stores
Authorization: Bearer <access-token>
```

无请求体。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `[]` 根数组 | array | 是 | 门店数组 |
| `id` | int | 是 | 门店 ID |
| `code` | string | 是 | 门店编码 |
| `name` | string | 是 | 门店名 |
| `display_name` | string | 是 | 门店展示名 |
| `status` | int | 是 | `1` 启用 / `2` 停用 |

**返回示例**

```json
[
  { "id": 1, "code": "hq", "name": "总店", "display_name": "总店（总部）", "status": 1 }
]
```

**返回示例（空数据）**

```json
[]
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。  
- 调用顺序：新增 / 编辑红娘表单的"门店"下拉在打开时调用。  
- 幂等：查询天然幂等。

**错误**：见通用错误（401 / 403）。

**文档完成自检清单**
- [x] 有请求参数表（无参数已标注）
- [x] 有完整请求体示例（无请求体时已明确标注）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例（GET 无 body 非法场景）

---

### 2.19 `POST /admin/common/upload` — 通用图片上传

**基本信息**
- 用途：通用图片上传（头像 / 海报素材等），自动转 WebP。  
- URL：`POST /api/v1/admin/common/upload`  
- Method：POST  
- 登录：必需  
- 权限：`matchmaker.manage`  
- Content-Type：`multipart/form-data`  
- 成功：200  

**请求参数（body，multipart）**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `file` | body | binary(file) | **是** | — | `≤ 5MB`；图片格式；服务端转 WebP | 待上传图片 |

**请求体示例**

```
POST /api/v1/admin/common/upload
Authorization: Bearer <access-token>
Content-Type: multipart/form-data; boundary=----boundary

------boundary
Content-Disposition: form-data; name="file"; filename="avatar.png"
Content-Type: image/png

<二进制图片数据>
------boundary--
```

无 JSON body（multipart）。非法示例：
- 未传 `file`（→ 422）。
- `file` 大小 > 5MB（→ 422 / 413）。
- 非图片文件（→ 422）。

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `url` | string | 是 | 上传后图片 URL（WebP） |
| `content_type` | string | 是 | 实际存储的 MIME（通常 `image/webp`） |
| `size` | int | 是 | 文件字节大小 |
| `purpose` | string? | 是 | 用途标识（如 `avatar` / `poster`），可为 `null` |

**返回示例**

```json
{
  "url": "https://cdn.example.com/upload/avatar/12.webp",
  "content_type": "image/webp",
  "size": 123456,
  "purpose": "avatar"
}
```

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`；`file` 必传且 ≤ 5MB。  
- 调用顺序：表单选择图片 → 先上传拿 `url` → 再将 `url` 作为 `avatar` 等字段提交到红娘新增 / 编辑接口。  
- 幂等：非幂等（每次上传生成新文件）；前端上传按钮防重复。  
- 边界场景：超过 5MB 或格式不支持被拒；WebP 转换失败返回 422 / 500。

**错误**：见通用错误（401 / 403 / 422 / 500）；超大文件可能为 413。

**文档完成自检清单**
- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（multipart 已说明，含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例
- [x] 有"使用方法与业务规则"小节
- [x] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [x] 至少一个非法参数示例

---

## 三、变更记录

| 日期 | 版本 | 变更 | 影响 |
| --- | --- | --- | --- |
| 2026-09-11 | v1.0 | 首次按 `PROJECT_RULES.md` 2.1.1 统一模板编写红娘管理 19 个接口完整契约 | — |

---

## 四、兼容性说明

- 本组为后台管理接口，独立红娘后台 Token 鉴权（`get_current_matchmaker_admin`），与用户端 Token 体系隔离。  
- 权限分层：红娘管理主接口 `matchmaker.manage`，字典接口 `matchmaker.read`；前端需分别申请对应权限点。  
- 金额 / 比例字段均为字符串，旧客户端若不解析字符串会导致展示 / 计算错误，需统一 `Number()` 解析。  
- `GET .../permissions` 与 `PUT .../permissions` 使用**驼峰**字段名（`matchmakerId` / `menuIds` 中的 `menuIds` 为驼峰），前端序列化需注意字段大小写。
