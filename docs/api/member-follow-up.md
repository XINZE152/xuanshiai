# 会员跟进全览与历史跟进导入（M3-5）管理后台接口

> 对应前端页面：**会员CRM → 跟进全览**（`/love-user-follow-up`）及其二级页 **导入历史跟进**（`/love-user-follow-up-import`）。
> 后端文件：`app/api/routes/member_follow_up_admin.py`、`app/services/member_follow_up_admin.py`、`app/schemas/member_follow_up_admin.py`。
> 测试文件：`tests/test_member_follow_up_admin_ext.py`。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 全局前缀 | `/api/v1` |
| 鉴权 | 红娘后台 Token：`Authorization: Bearer <access_token>`，依赖函数 `get_current_matchmaker_admin` |
| 读权限 | `matchmaker.member.read` |
| 写权限 | `matchmaker.member.manage` |
| 成功响应 | **不包裹 `data`**，直接返回业务对象 |
| 分页结构 | `{ items, page, page_size, total, has_more }` |
| 错误结构 | `HTTPException(status_code, detail)` → `{ "detail": "..." }`，**无业务错误码字段** |
| 时间戳 | 由 MySQL 写入（`DEFAULT CURRENT_TIMESTAMP`），接口返回的 `created_at` 为 datetime |
| 会员编号 | 项目无 `member_code` 列，统一由 `CONCAT('G', LPAD(users.id, 6, '0'))` 生成 |

### 1.1 与既有接口的关系

本模块**新增独立端点**，不修改既有行为：

| 既有端点 | 归属 | 说明 |
|---|---|---|
| `GET /api/v1/admin/members/{member_id}/follow-ups` | 单会员跟进（会员快捷资料抽屉在用） | 本模块未改动 |
| `POST /api/v1/admin/members/{member_id}/follow-ups` | 新增单条跟进 | 本模块未改动 |
| `GET /api/v1/admin/members/follow-ups` | **本模块**（跟进全览） | 由「裸 SQL 返回」升级为强类型 `MemberFollowUpListPage`，并补齐筛选参数 |

> ⚠️ 前端 `admin-endpoints.ts` 中历史方法 `memberFollowUpsOverview` 仍指向同一路径、返回弱类型，客源线索模块（`/customer-follow-up`）在用，**未删除**。会员CRM 跟进全览请使用新方法 `memberFollowUpList`。

### 1.2 数据源

| 数据 | 来源 |
|---|---|
| 跟进记录 | `member_follow_up(id, user_id, method, content, next_follow_at, created_by, created_at)` |
| 会员信息 | `users(id, nickname, avatar, phone)` |
| 客户意向 | `user_profile.intention_level`（1 低 / 2 中 / 3 高） |
| 会员实名 | `user_auth.real_name`（用于关键字匹配） |
| 跟进人称呼 | `matchmaker_admin_account.display_name` → 回退 `users.nickname` → 回退 `admin` |

> 「是否由系统自动生成的记录」当前 `member_follow_up` **无标记列**，故 `note` 字段**恒为 `null`**（前端不展示该行提示）。

---

## 2. 跟进全览

### 2.1 `GET /api/v1/admin/members/follow-ups`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/members/follow-ups` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 分页查询全平台会员跟进记录，供「跟进全览」表格渲染 |
| 返回 | `MemberFollowUpListPage` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `page` | query | int | 否 | `1` | `ge=1, le=1000` | 页码 |
| `page_size` | query | int | 否 | `20` | `ge=1, le=100` | 每页条数 |
| `keyword` | query | string | 否 | — | `max_length=64` | 关键字，匹配会员昵称 / 手机号 / 实名 / 会员编号 |
| `intention_level` | query | int | 否 | — | `ge=1, le=3` | 客户意向筛选：1 低 / 2 中 / 3 高 |
| `start_date` | query | string | 否 | — | 正则 `^\d{4}-\d{2}-\d{2}$` | 跟进时间起（**含**，按天） |
| `end_date` | query | string | 否 | — | 正则 `^\d{4}-\d{2}-\d{2}$` | 跟进时间止（**含**，按天，后端换算为 `< end_date + 1 天`） |

#### 请求示例

合法：

```http
GET /api/v1/admin/members/follow-ups?page=1&page_size=20&keyword=G396140&intention_level=2&start_date=2026-09-01&end_date=2026-09-11
Authorization: Bearer <access_token>
```

非法示例（日期格式错误 → 422）：

```http
GET /api/v1/admin/members/follow-ups?start_date=2026/09/01
```

非法示例（意向越界 → 422）：

```http
GET /api/v1/admin/members/follow-ups?intention_level=5
```

#### 返回参数

`MemberFollowUpListPage`：

| 字段 | 类型 | 必返 | 业务含义 |
|---|---|---|---|
| `items` | `MemberFollowUpRow[]` | 是 | 当前页数据 |
| `page` | int | 是 | 当前页码 |
| `page_size` | int | 是 | 每页条数 |
| `total` | int | 是 | 符合条件的总条数 |
| `has_more` | bool | 是 | 是否还有下一页 |

`MemberFollowUpRow`：

| 字段 | 类型 | 可为空 | 业务含义 | 前端列 |
|---|---|---|---|---|
| `id` | int | 否 | 跟进记录 ID | — |
| `user_id` | int | 否 | 会员用户 ID | — |
| `member_code` | string | 否 | 会员编号，`G`+6 位 | 「跟进会员」副标题 |
| `nickname` | string | 是 | 会员昵称 | 「跟进会员」主标题 |
| `avatar` | string | 是 | 会员头像相对路径 | 「跟进会员」头像 |
| `matchmaker_name` | string | 否 | 跟进红娘称呼，取不到回退 `admin` | 「跟进红娘」列 |
| `method` | string | 否 | 跟进方式原始枚举 `PHONE/WECHAT/VISIT/OTHER` | — |
| `method_label` | string | 否 | 跟进方式中文：电话 / 微信 / 面谈 / 其他 | — |
| `content` | string | 否 | 跟进内容 | 「跟进内容」正文 |
| `note` | string | 是 | 系统提示文案；**当前数据源不支持，恒为 `null`** | 「跟进内容」副行（有值才显示） |
| `intention_level` | int | 是 | 客户意向 1/2/3 | — |
| `created_at` | datetime | 是 | 跟进时间 | 「跟进时间」列 |

#### 返回示例

```json
{
  "items": [
    {
      "id": 128,
      "user_id": 396140,
      "member_code": "G396140",
      "nickname": "普浩芸",
      "avatar": "/storage/avatar/396140.webp",
      "matchmaker_name": "芸希老师",
      "method": "PHONE",
      "method_label": "电话",
      "content": "电话沟通，客户反馈本周有空到店，已约定周六下午",
      "note": null,
      "intention_level": 3,
      "created_at": "2026-09-01T14:48:54"
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
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

#### 使用方法与业务规则

- 排序固定为 `f.created_at DESC, f.id DESC`（最新在前）。
- `start_date` / `end_date` 为**闭区间**（含首尾当天）；仅传 `start_date` 表示「该日起至今」，仅传 `end_date` 表示「截止该日」。
- 前端 8 个时间快捷卡（今天/昨天/最近3天/本周/上周/本月/上月）在本页**由前端换算为 `start_date`/`end_date`** 后请求本接口；若用户又手动选择了日期范围，以手动日期优先。
- `keyword` 为模糊匹配（`LIKE %kw%`），同时覆盖昵称、手机号、实名、会员编号。
- 分页为空时返回 `items: []`、`total: 0`，不报错。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未携带 / Token 失效 | `adminApi` 自动清 token 并跳 `/login` | `{"detail":"未认证"}` |
| 403 | 无 `matchmaker.member.read` | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 422 | 日期格式非 `YYYY-MM-DD`、`intention_level` 越界、`page_size > 100` | 校正筛选条件后重试 | `{"detail":[{"loc":["query","intention_level"],"msg":"Input should be less than or equal to 3","type":"less_than_equal"}]}` |

#### 文档完成自检清单

- [x] 基本信息（路径 / 方法 / 权限 / 用途 / 返回）
- [x] 请求参数表（含位置 / 类型 / 必填 / 默认 / 校验 / 业务含义）
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（分页 + 行，逐字段业务含义）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则
- [x] 错误表（状态码 + 触发条件 + 前端建议 + 响应 JSON）

---

### 2.2 `GET /api/v1/admin/members/follow-ups/summary`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/members/follow-ups/summary` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 「跟进全览」顶部 8 个时间统计卡的计数 |
| 返回 | `MemberFollowUpSummary` |

#### 请求参数

无。

#### 请求示例

```http
GET /api/v1/admin/members/follow-ups/summary
Authorization: Bearer <access_token>
```

#### 返回参数

| 字段 | 类型 | 业务含义 | 前端统计卡 |
|---|---|---|---|
| `all` | int | 全部跟进记录数 | 全部 |
| `today` | int | 今天（`CURDATE()`） | 今天 |
| `yesterday` | int | 昨天 | 昨天 |
| `three_days` | int | 最近 3 天（含今天，`>= CURDATE()-2`） | 最近3天 |
| `this_week` | int | 本周（周一为起点，`>= 本周一`） | 本周 |
| `last_week` | int | 上周（`[上周一, 本周一)`） | 上周 |
| `this_month` | int | 本月（按 `%Y-%m` 匹配） | 本月 |
| `last_month` | int | 上月（按 `%Y-%m` 匹配上月） | 上月 |

> 各维度口径**互相独立、不做互斥**，因此 `today + yesterday` 不等于 `three_days`，属预期行为。

#### 返回示例

```json
{
  "all": 1280,
  "today": 12,
  "yesterday": 9,
  "three_days": 34,
  "this_week": 58,
  "last_week": 61,
  "this_month": 203,
  "last_month": 187
}
```

空数据示例（库中无任何跟进记录）：

```json
{
  "all": 0, "today": 0, "yesterday": 0, "three_days": 0,
  "this_week": 0, "last_week": 0, "this_month": 0, "last_month": 0
}
```

#### 使用方法与业务规则

- 统计基于 `member_follow_up.created_at`，与列表接口同源。
- 周口径以**周一为起点**（`WEEKDAY()`，0=周一），避免 `YEARWEEK` 跨年边界问题。
- 统计为**全平台口径**，不随列表筛选变化；筛选仅影响列表。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未携带 / Token 失效 | `adminApi` 自动跳登录 | `{"detail":"未认证"}` |
| 403 | 无 `matchmaker.member.read` | 展示「无权限」 | `{"detail":"无权限访问"}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数（无参，已说明）
- [x] 请求示例
- [x] 返回参数表（逐字段业务含义 + 对应统计卡）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则（含「口径不互斥」说明）
- [x] 错误表

---

## 3. 历史跟进批量导入

### 3.1 `GET /api/v1/admin/members/follow-ups/import-template`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/members/follow-ups/import-template` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 下载「导入历史跟进」页第一步的 Excel 模板 |
| 返回 | **二进制 `.xlsx`**（非 JSON） |

#### 请求参数

无。

#### 请求示例

```http
GET /api/v1/admin/members/follow-ups/import-template
Authorization: Bearer <access_token>
```

#### 返回参数

非 JSON。响应头：

| 响应头 | 值 |
|---|---|
| `Content-Type` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `Content-Disposition` | `attachment; filename="follow-up-import-template.xlsx"` |

工作簿结构：

| Sheet | 内容 |
|---|---|
| `历史跟进` | 第 1 行表头，第 2 行示例数据 |

表头列（顺序即列顺序）：

| 列 | 表头 | 说明 |
|---|---|---|
| A | `会员手机号` | **必填**，按此识别会员（精确匹配 `users.phone` 且 `status=1`） |
| B | `跟进内容` | **必填**，为空该行失败 |
| C | `跟进人` | 选填，匹配后台账号称呼，匹配不到或为空 → `admin` |
| D | `跟进时间` | 选填，支持多种格式，无法解析 → 使用导入时间 |
| E | `跟进方式` | 选填，中文/英文别名均可，无法识别 → `其他` |

#### 返回示例

> 二进制流，无 JSON。浏览器保存为 `follow-up-import-template.xlsx`。

#### 使用方法与业务规则

- 该接口返回**二进制**，`adminApi` 的 `response.json()` 无法消费，因此前端在 `admin-endpoints.ts` 中单独提供 `downloadMemberFollowUpTemplate()`：携带后台 token 拉取 `Blob` 后触发浏览器下载。
- 模板表头允许用户微调（后端按包含匹配定位列，如「手机」「电话」「phone」任一命中即可）；但**缺少「手机号」或「跟进内容」列会直接 400**。
- 表头校验发生在下载之外（导入时），本接口固定输出标准模板。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未携带 / Token 失效 | `adminApi` 自动跳登录 | `{"detail":"未认证"}` |
| 403 | 无 `matchmaker.member.read` | 展示「无权限」 | `{"detail":"无权限访问"}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数（无参，已说明）
- [x] 请求示例
- [x] 返回参数（非 JSON，已说明响应头 + 模板结构）
- [x] 返回示例（二进制，已说明）
- [x] 使用方法与业务规则（含前端消费方式）
- [x] 错误表

---

### 3.2 `POST /api/v1/admin/members/follow-ups/import`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/members/follow-ups/import` |
| 方法 | `POST` |
| 权限 | `matchmaker.member.manage` |
| 用途 | 按模板批量写入历史跟进记录 |
| 请求体 | `multipart/form-data`，字段 `file` |
| 返回 | `MemberFollowUpImportResult` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 校验 | 业务含义 |
|---|---|---|---|---|---|
| `file` | form-data | file | 是 | 文件名须以 `.xlsx` 结尾；`< 2000` 数据行 | 按模板填写的 Excel |

#### 请求示例

合法：

```http
POST /api/v1/admin/members/follow-ups/import
Authorization: Bearer <access_token>
Content-Type: multipart/form-data; boundary=----WebKitFormBoundary

------WebKitFormBoundary
Content-Disposition: form-data; name="file"; filename="follow-ups.xlsx"
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet

<二进制内容>
------WebKitFormBoundary--
```

非法示例（旧版 `.xls` → 400）：

```http
POST /api/v1/admin/members/follow-ups/import
...(filename="follow-ups.xls")
```

非法示例（缺少「跟进内容」列 → 400）：

```http
POST /api/v1/admin/members/follow-ups/import
...(表头仅有「姓名」「备注」)
```

#### 返回参数

`MemberFollowUpImportResult`：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `created` | int | 成功写入条数 |
| `skipped` | int | 跳过条数（整行手机号与内容均为空） |
| `failed` | int | 失败条数（缺手机号 / 缺内容 / 手机号无对应会员 / 单行写入异常） |
| `errors` | string[] | 失败明细，格式 `第 N 行：原因`；**最多返回前 50 条** |

#### 返回示例

全部成功：

```json
{ "created": 42, "skipped": 0, "failed": 0, "errors": [] }
```

部分失败：

```json
{
  "created": 40,
  "skipped": 1,
  "failed": 2,
  "errors": [
    "第 5 行：手机号 13900000001 在会员CRM中不存在，无法导入",
    "第 9 行：缺少跟进内容"
  ]
}
```

空数据示例（表头正确但无数据行）：

```json
{ "created": 0, "skipped": 0, "failed": 0, "errors": [] }
```

#### 使用方法与业务规则

调用顺序：先 `GET .../import-template` 下载模板 → 线下填写 → `POST .../import`。

逐行处理规则（与页面「须知」一致）：

| 场景 | 结果 |
|---|---|
| 手机号与跟进内容均为空 | `skipped + 1` |
| 手机号为空 | `failed + 1`，`errors` 记「缺少会员手机号」 |
| 跟进内容为空 | `failed + 1`，`errors` 记「缺少跟进内容」 |
| 手机号在 `users` 无匹配（`status=1`） | `failed + 1`，`errors` 记「手机号 X 在会员CRM中不存在，无法导入」 |
| 「跟进人」匹配不到后台账号 | 回退 `admin`（优先 `matchmaker_admin_account.username='admin'`，否则当前操作人） |
| 「跟进时间」无法解析或为空 | 使用导入时间（数据库 `DEFAULT CURRENT_TIMESTAMP`） |
| 「跟进方式」无法识别 | 记为 `OTHER`（其他） |

其他规则：

- **单行失败不阻断整批**：某行写入异常时记入 `errors` 并继续处理后续行。
- **单次上限 2000 行**，超出直接 400，需拆分文件。
- 每成功写入一行会写一条 `business_audit_log`（`action='member.follow_up.import'`）。
- 文件为空（0 字节）→ 400「上传文件为空」。
- 仅支持 `.xlsx`；`.xls` 与其它扩展名 → 400。
- 幂等性：**无幂等键**，重复导入同一文件会产生重复记录（属预期，历史数据补录场景）。

时间格式支持：`YYYY-MM-DD HH:mm:ss`、`YYYY-MM-DD HH:mm`、`YYYY-MM-DD`、`YYYY/MM/DD[ HH:mm[:ss]]`、`YYYY.MM.DD[ HH:mm:ss]`；Excel 原生日期单元格直接取用。

跟进方式别名：`电话|phone` → `PHONE`；`微信|wechat|vx` → `WECHAT`；`面谈|拜访|到店|visit` → `VISIT`；`其他|other` → `OTHER`。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 400 | 文件为空 / 非 `.xlsx` / 表头缺列 / 超过 2000 行 / Excel 损坏 | `showConfigToast(detail, "error")` | `{"detail":"模板表头缺少「跟进内容」列，请使用下载的模板填写"}` |
| 401 | 未携带 / Token 失效 | `adminApi` 自动跳登录 | `{"detail":"未认证"}` |
| 403 | 无 `matchmaker.member.manage` | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 422 | 未提交 `file` 字段 | 提示「请先上传 EXCEL 文件」 | `{"detail":[{"loc":["body","file"],"msg":"Field required","type":"missing"}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（逐字段业务含义）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则（调用顺序 / 逐行规则 / 上限 / 幂等 / 格式兼容）
- [x] 错误表

---

## 4. 总览：本模块全部端点

| 方法 | 路径 | 权限 | 用途 | 返回 |
|---|---|---|---|---|
| GET | `/api/v1/admin/members/follow-ups` | `matchmaker.member.read` | 跟进全览分页列表 | `MemberFollowUpListPage` |
| GET | `/api/v1/admin/members/follow-ups/summary` | `matchmaker.member.read` | 8 个时间统计卡 | `MemberFollowUpSummary` |
| GET | `/api/v1/admin/members/follow-ups/import-template` | `matchmaker.member.read` | 下载导入模板 | `.xlsx` 二进制 |
| POST | `/api/v1/admin/members/follow-ups/import` | `matchmaker.member.manage` | 批量导入历史跟进 | `MemberFollowUpImportResult` |

> **路由顺序**：三条静态子路径（`/summary`、`/import-template`、`/import`）均声明在 `/{member_id}/follow-ups` 之前，避免被路径参数吃掉。

依赖变更：新增第三方库 `openpyxl`（Excel 读写在服务端完成），已通过 `uv add openpyxl` 写入 `pyproject.toml` / `uv.lock`。

---

## 5. 变更记录

| 日期 | 变更 | 变更前 | 变更后 | 影响范围 |
|---|---|---|---|---|
| 2026-09-11 | 跟进全览列表接口强化 | `GET /admin/members/follow-ups` 裸 SQL 返回弱类型 dict，仅支持 `search` | 强类型 `MemberFollowUpListPage`；新增 `keyword`/`intention_level`/`start_date`/`end_date`；行内新增 `member_code`/`avatar`/`matchmaker_name`/`method_label`/`intention_level`/`note` | 前端 `memberFollowUpList`；旧 `memberFollowUpsOverview`（客源线索页在用）行为不变 |
| 2026-09-11 | 新增 3 个端点 | — | `GET /follow-ups/summary`、`GET /follow-ups/import-template`、`POST /follow-ups/import` | 会员CRM「跟进全览」「导入历史跟进」两页 |
| 2026-09-11 | 新增依赖 | — | `openpyxl == 3.1.5` | 后端 Excel 解析/生成 |
