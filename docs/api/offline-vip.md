# 线下VIP会员服务（M3-6）管理后台接口

> 对应前端页面：**会员CRM → 线下VIP**（`/love-user-vip-underline`）。
> 后端文件：`app/api/routes/offline_vip_admin.py`、`app/services/offline_vip_admin.py`、`app/schemas/offline_vip_admin.py`。
> 测试文件：`tests/test_offline_vip_admin_ext.py`。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 全局前缀 | `/api/v1` |
| 鉴权 | 红娘后台 Token：`Authorization: Bearer <access_token>`，依赖 `get_current_matchmaker_admin` |
| 读权限 | `matchmaker.member.read` |
| 写权限 | `matchmaker.member.manage` |
| 权限映射 | `app/api/dependencies.py:_matchmaker_admin_permission` 新增规则：路径含 `/offline-vips` → GET 走 `matchmaker.member.read`，非 GET 走 `matchmaker.member.manage` |
| 成功响应 | **不包裹 `data`** |
| 分页结构 | `{ items, page, page_size, total, has_more }` |
| 错误结构 | `HTTPException(status_code, detail)` → `{ "detail": "..." }`，**无业务错误码字段** |
| 金额 | `contract_amount` 为 `Decimal`，JSON 中序列化为**字符串**（如 `"19800.00"`） |
| 会员编号 | 无 `member_code` 列，统一 `CONCAT('G', LPAD(users.id, 6, '0'))` |

### 1.1 数据源

| 数据 | 来源 |
|---|---|
| 服务记录 | `offline_vip`（`user_id / sales_matchmaker_id / service_matchmaker_id / promoter_id / sign_date / service_start / service_end / package_name / contract_amount / promise_meet_count / success_meet_count / progress / contract_status / contract_no / remark / attach_urls / created_by`，软删 `deleted_at`） |
| 成功约见修改记录 | `offline_vip_meet_log(vip_id, before_count, after_count, remark, changed_by, created_at)` |
| 会员信息 | `users(nickname, avatar, phone)`、`user_auth(real_name)` |
| 红娘 | `matchmaker_profile` JOIN `users`（服务红娘 / 销售红娘同池） |
| 推广红娘 | `user_matchmaker_apply(application_type='promoter', status=1)` JOIN `users` |
| 最近跟进 | `member_follow_up` 该会员最新 `created_at` |
| 门店数 | `organization(org_type='store', status=1)` |

> **销售红娘 / 服务红娘同源**：`love-matchmaker-list` 页面的工作汇报说明「线下业绩 = 在线下VIP中作为“销售红娘”的合同金额」，即二者都是总店服务红娘体系（`matchmaker_profile`）内的成员，故两个下拉返回同一集合；区别仅在记录里落在哪一列。

### 1.2 服务进度枚举

| 值 | 中文 | 对应前端子 Tab |
|---|---|---|
| `matching` | 匹配推荐中 | 匹配推荐中 |
| `dating` | 约会进行中 | 约会进行中 |
| `deep` | 深度接触 | 深度接触 |
| `in_love` | 已经恋爱 | 已经恋爱 |
| `met_parents` | 已见父母 | 已见父母 |
| `paused` | 暂停服务 | 暂停服务 |
| `breakup` | 恋爱分手 | 恋爱分手 |
| `married` | 已经领证 | 已经领证 |

电子合同状态枚举：`none` 未发起 / `pending` 签署中 / `signed` 已签署 / `void` 已作废。

---

## 2. 列表

### 2.1 `GET /api/v1/admin/offline-vips`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 线下VIP 列表分页 |
| 返回 | `OfflineVipPage` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `page` | query | int | 否 | `1` | `ge=1, le=1000` | 页码 |
| `page_size` | query | int | 否 | `20` | `ge=1, le=100` | 每页条数 |
| `progress` | query | string | 否 | — | 枚举正则（见 1.2） | 服务进度；不传为「全部」 |
| `sales_matchmaker_id` | query | int | 否 | — | `ge=1` | 销售红娘 |
| `service_matchmaker_id` | query | int | 否 | — | `ge=1` | 服务红娘 |
| `promoter_id` | query | int | 否 | — | `ge=1` | 推广红娘 |
| `sign_start` | query | string | 否 | — | 正则 `^\d{4}-\d{2}-\d{2}$` | 签约日期起（含） |
| `sign_end` | query | string | 否 | — | 正则 `^\d{4}-\d{2}-\d{2}$` | 签约日期止（含） |
| `keyword` | query | string | 否 | — | `max_length=64` | 会员昵称 / 手机 / 姓名 / 编号 |

#### 请求示例

合法：

```http
GET /api/v1/admin/offline-vips?page=1&page_size=20&progress=dating&service_matchmaker_id=42&sign_start=2026-01-01&sign_end=2026-09-11&keyword=G396140
Authorization: Bearer <access_token>
```

非法示例（进度枚举非法 → 422）：

```http
GET /api/v1/admin/offline-vips?progress=unknown
```

非法示例（日期格式错误 → 422）：

```http
GET /api/v1/admin/offline-vips?sign_start=2026/01/01
```

#### 返回参数

`OfflineVipPage`：`items / page / page_size / total / has_more`（含义同全局分页约定）。

`OfflineVipItem`：

| 字段 | 类型 | 可为空 | 业务含义 | 前端列 |
|---|---|---|---|---|
| `id` | int | 否 | 记录 ID | ID |
| `user_id` | int | 否 | 会员用户 ID | — |
| `member_code` | string | 否 | 会员编号 `G`+6 位 | 会员 |
| `nickname` | string | 是 | 会员昵称 | 会员 |
| `avatar` | string | 是 | 会员头像 | 会员 |
| `phone` | string | 是 | 会员手机号 | — |
| `sign_date` | date | 是 | 签约日期 | 签约日期 |
| `package_name` | string | 是 | 服务套餐 | 服务套餐 |
| `progress` | string | 否 | 服务进度枚举 | 服务进度 |
| `progress_label` | string | 否 | 服务进度中文 | 服务进度 |
| `service_start` | date | 是 | 服务开始日期 | — |
| `service_end` | date | 是 | 服务结束日期 | — |
| `last_follow_at` | datetime | 是 | 该会员最近一次跟进时间 | 最近跟进 |
| `contract_amount` | string | 否 | 合同金额（Decimal→字符串） | 合同金额 |
| `sales_matchmaker_id` | int | 是 | 销售红娘 | — |
| `sales_matchmaker_name` | string | 是 | 销售红娘称呼 | 红娘（备选） |
| `service_matchmaker_id` | int | 是 | 服务红娘 | — |
| `service_matchmaker_name` | string | 是 | 服务红娘称呼 | 红娘（首选） |
| `promoter_id` | int | 是 | 推广红娘 | — |
| `promoter_name` | string | 是 | 推广红娘称呼 | — |
| `contract_status` | string | 否 | 电子合同状态枚举 | 电子合同 |
| `contract_status_label` | string | 否 | 电子合同状态中文 | 电子合同 |
| `contract_no` | string | 是 | 电子合同编号 | — |
| `promise_meet_count` | int | 否 | 承诺约见人数 | — |
| `success_meet_count` | int | 否 | 已成功约见人数 | — |
| `remark` | string | 是 | 备注信息 | 备注信息 |
| `attach_urls` | string[] | 否 | 图片附件地址列表 | — |
| `created_at` | datetime | 是 | 创建时间 | — |
| `updated_at` | datetime | 是 | 更新时间 | — |

#### 返回示例

```json
{
  "items": [
    {
      "id": 12,
      "user_id": 396140,
      "member_code": "G396140",
      "nickname": "普浩芸",
      "avatar": "/storage/avatar/396140.webp",
      "phone": "13800000000",
      "sign_date": "2026-09-01",
      "package_name": "尊享服务套餐",
      "progress": "dating",
      "progress_label": "约会进行中",
      "service_start": "2026-09-01",
      "service_end": "2027-09-01",
      "last_follow_at": "2026-09-10T10:20:30",
      "contract_amount": "19800.00",
      "sales_matchmaker_id": 42,
      "sales_matchmaker_name": "芸希老师",
      "service_matchmaker_id": 43,
      "service_matchmaker_name": "琴琴",
      "promoter_id": null,
      "promoter_name": null,
      "contract_status": "signed",
      "contract_status_label": "已签署",
      "contract_no": "HT202609010001",
      "promise_meet_count": 6,
      "success_meet_count": 2,
      "remark": "客户偏好本地户籍",
      "attach_urls": ["/storage/contract/12-a.webp"],
      "created_at": "2026-09-01T09:00:00",
      "updated_at": "2026-09-10T10:20:30"
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

- 排序固定 `v.id DESC`；仅返回 `deleted_at IS NULL` 的记录。
- `progress` 为空表示不限；前端 9 个子 Tab 的「全部」对应不传该参数。
- `keyword` 同时匹配昵称、手机、会员编号、实名（`user_auth.real_name`）。
- `last_follow_at` 来自 `member_follow_up` 该会员最新一条记录时间；无跟进记录时为 `null`。
- 「红娘」列优先展示服务红娘称呼，服务红娘为空时回退销售红娘。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 / Token 失效 | `adminApi` 自动跳 `/login` | `{"detail":"请先登录红娘后台"}` |
| 403 | 无 `matchmaker.member.read` | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 422 | `progress` 非法、日期格式错误、`page_size > 100` | 校正筛选条件 | `{"detail":[{"loc":["query","progress"],"msg":"String should match pattern ...","type":"string_pattern_mismatch"}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（逐字段业务含义）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则
- [x] 错误表

---

## 3. 统计卡

### 3.1 `GET /api/v1/admin/offline-vips/statistics`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips/statistics` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 顶部 10 张统计卡 |
| 返回 | `OfflineVipStatistics` |

#### 请求参数

无。

#### 请求示例

```http
GET /api/v1/admin/offline-vips/statistics
Authorization: Bearer <access_token>
```

#### 返回参数

| 字段 | 类型 | 业务含义 | 统计卡 | 口径 |
|---|---|---|---|---|
| `store_count` | int | 门店数 | 门店 | `organization(org_type='store', status=1)` 计数 |
| `vip_count` | int | 线下VIP 总数 | 线下VIP | `offline_vip` 未删除记录数 |
| `serving_count` | int | 服务中 | 服务中 | `service_end >= CURDATE()` 且 `progress NOT IN ('breakup','married')` |
| `expiring_count` | int | 即将到期 | 即将到期 | `service_end` ∈ [今天, 今天+30 天] |
| `expired_count` | int | 服务到期 | 服务到期 | `service_end < CURDATE()` |
| `paid_count` | int | 有偿费 | 有偿费 | `contract_amount > 0` |
| `promise_meet_total` | int | 总计安排见面 | 总计安排见面 | `SUM(promise_meet_count)` |
| `promise_meet_month` | int | 本月已安排 | 本月已安排 | 本月创建记录的 `promise_meet_count` 合计 |
| `refund_risk_count` | int | 有退费风险 | 有退费风险 | `progress IN ('paused','breakup')` |
| `refunded_count` | int | 已退费 | 已退费 | **`offline_vip` 无退费字段，无数据源，恒为 0** |

#### 返回示例

```json
{
  "store_count": 3,
  "vip_count": 128,
  "serving_count": 96,
  "expiring_count": 11,
  "expired_count": 20,
  "paid_count": 128,
  "promise_meet_total": 720,
  "promise_meet_month": 42,
  "refund_risk_count": 7,
  "refunded_count": 0
}
```

空数据示例：全 0。

#### 使用方法与业务规则

- 统计为**全平台口径**，不随列表筛选变化。
- `refunded_count` 恒为 `0`（无数据源），已在返回字段说明中标注；若后续引入退费流程需同步补充。
- 各维度**互相独立、不做互斥**（例如「服务中」与「即将到期」可能同时计一条）。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无读权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数（无参）
- [x] 请求示例
- [x] 返回参数表（含真实 SQL 口径）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则
- [x] 错误表

---

## 4. 下拉选项

### 4.1 `GET /api/v1/admin/offline-vips/options`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips/options` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 筛选区 3 个下拉 + 抽屉内 3 个下拉 + 套餐类型选项 |
| 返回 | `OfflineVipOptions`（**纯对象，非分页**） |

#### 请求参数

无。

#### 请求示例

```http
GET /api/v1/admin/offline-vips/options
Authorization: Bearer <access_token>
```

#### 返回参数

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `sales_matchmakers` | `OfflineVipOption[]` | 销售红娘选项（总店服务红娘池） |
| `service_matchmakers` | `OfflineVipOption[]` | 服务红娘选项（同上，同源） |
| `promoters` | `OfflineVipOption[]` | 在职推广红娘选项 |
| `packages` | `string[]` | 套餐类型选项（库中已用套餐 + 默认兜底套餐） |

`OfflineVipOption`：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `id` | int | `users.id`，提交时作为 `sales_matchmaker_id` / `service_matchmaker_id` / `promoter_id` |
| `name` | string | 展示名（`users.nickname`；推广红娘取 `real_name` 优先） |
| `extra` | string \| null | 红娘的「超级红娘 / 普通红娘」标注；推广红娘为 `null` |

#### 返回示例

```json
{
  "sales_matchmakers": [
    { "id": 42, "name": "芸希老师", "extra": "超级红娘" },
    { "id": 43, "name": "琴琴", "extra": "普通红娘" }
  ],
  "service_matchmakers": [
    { "id": 42, "name": "芸希老师", "extra": "超级红娘" },
    { "id": 43, "name": "琴琴", "extra": "普通红娘" }
  ],
  "promoters": [{ "id": 88, "name": "小美", "extra": null }],
  "packages": ["尊享服务套餐", "标准服务套餐", "基础服务套餐", "私人定制套餐"]
}
```

空数据示例：

```json
{ "sales_matchmakers": [], "service_matchmakers": [], "promoters": [], "packages": ["基础服务套餐", "标准服务套餐", "尊享服务套餐", "私人定制套餐"] }
```

#### 使用方法与业务规则

- `sales_matchmakers` 与 `service_matchmakers` **内容相同**（同一总店红娘池），前端分两个下拉仅为区分业务语义。
- `packages` 先取 `offline_vip.package_name` 的去重值，再补 4 个默认套餐，保证下拉永不为空。
- 只返回在职红娘（`users.status=1`）与在职推广红娘（`application_type='promoter' AND status=1`）。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无读权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数（无参）
- [x] 请求示例
- [x] 返回参数表（含嵌套展开）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则
- [x] 错误表

---

## 5. 新增

### 5.1 `POST /api/v1/admin/offline-vips`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips` |
| 方法 | `POST` |
| 权限 | `matchmaker.member.manage` |
| 用途 | 添加线下VIP会员服务信息 |
| 请求体 | `application/json` |
| 返回 | `OfflineVipItem`（201） |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `user_id` | body | int | 否 | — | `ge=1` | 直接指定会员（优先于 `lookup`） |
| `lookup` | body | string | 否 | — | `1..100` | 会员账号检索词（昵称/手机/姓名/编号） |
| `lookup_by` | body | string | 否 | `nickname` | `nickname` / `phone` | `nickname` 按昵称/姓名/编号包含匹配；`phone` 按手机号精确匹配 |
| `sales_matchmaker_id` | body | int | 否 | — | `ge=1` | 销售红娘 |
| `service_matchmaker_id` | body | int | 否 | — | `ge=1` | 服务红娘 |
| `promoter_id` | body | int | 否 | — | `ge=1` | 推广红娘 |
| `sign_date` | body | date | 否 | — | `YYYY-MM-DD` | 签约日期 |
| `service_start` | body | date | 否 | — | `YYYY-MM-DD` | 服务开始日期 |
| `service_end` | body | date | 否 | — | `YYYY-MM-DD` | 服务结束日期 |
| `package_name` | body | string | 否 | — | `≤128` | 服务套餐 |
| `contract_amount` | body | decimal | 否 | `0.00` | `0 ~ 100000000` | 合同金额（元） |
| `promise_meet_count` | body | int | 否 | `0` | `0 ~ 100000` | 承诺约见人数 |
| `success_meet_count` | body | int | 否 | `0` | `0 ~ 100000` | 已成功约见人数 |
| `remark` | body | string | 否 | — | `≤500` | 备注信息 |
| `attach_urls` | body | string[] | 否 | `[]` | — | 图片附件地址（先经 `/admin/common/upload` 上传） |

#### 请求示例

合法：

```json
{
  "lookup": "普浩芸",
  "lookup_by": "nickname",
  "sales_matchmaker_id": 42,
  "service_matchmaker_id": 43,
  "sign_date": "2026-09-01",
  "service_start": "2026-09-01",
  "service_end": "2027-09-01",
  "package_name": "尊享服务套餐",
  "contract_amount": "19800.00",
  "promise_meet_count": 6,
  "success_meet_count": 0,
  "remark": "客户偏好本地户籍",
  "attach_urls": ["/storage/contract/a.webp"]
}
```

非法示例（既无 `user_id` 也无 `lookup` → 400）：

```json
{ "sales_matchmaker_id": 42 }
```

非法示例（金额为负 → 422）：

```json
{ "lookup": "普浩芸", "contract_amount": "-1" }
```

#### 返回参数

同 `OfflineVipItem`（见 2.1）。

#### 返回示例

```json
{ "id": 12, "user_id": 396140, "member_code": "G396140", "progress": "matching", "progress_label": "匹配推荐中", "contract_amount": "19800.00", "...": "其余字段见 2.1" }
```

#### 使用方法与业务规则

- 会员绑定顺序：传 `user_id` 直接校验 `users(status=1)` 存在，否则 404；否则用 `lookup` 检索（`nickname` 模式匹配昵称/编号/实名，`phone` 模式精确匹配手机号），取最新一条；两者都不传 → **400**「请填写会员账号（昵称/手机/姓名/编号）」。
- `lookup` 检索不到 → **404**「未找到匹配的会员，请核对昵称/手机/姓名/编号」。
- 同一会员已有未删除的线下VIP记录 → **409**「该会员已存在线下VIP记录，请直接编辑」。
- 新增后 `progress` 默认 `matching`、`contract_status` 默认 `none`。
- 写入 `business_audit_log`（`action='offline_vip.create'`）。
- 前端「会员账号」为单选输入，实际提交 `lookup`；若输入是 6 位以上纯数字则按手机号检索，否则按昵称。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 400 | 未提供 `user_id` 与 `lookup` | toast 提示填写会员账号 | `{"detail":"请填写会员账号（昵称/手机/姓名/编号）"}` |
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无写权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 404 | `user_id` 不存在 / `lookup` 无匹配 | toast 提示核对 | `{"detail":"未找到匹配的会员，请核对昵称/手机/姓名/编号"}` |
| 409 | 该会员已有线下VIP记录 | toast 提示改走编辑 | `{"detail":"该会员已存在线下VIP记录，请直接编辑"}` |
| 422 | 金额/人数越界、`remark` 超长 | 校正表单 | `{"detail":[{"loc":["body","contract_amount"],"msg":"Input should be greater than or equal to 0"}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（引用 2.1）
- [x] 返回示例
- [x] 使用方法与业务规则
- [x] 错误表

---

## 6. 详情

### 6.1 `GET /api/v1/admin/offline-vips/{vip_id}`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips/{vip_id}` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 编辑抽屉回填 |
| 返回 | `OfflineVipItem` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 校验 | 业务含义 |
|---|---|---|---|---|---|
| `vip_id` | path | int | 是 | `ge=1` | 线下VIP 记录 ID |

#### 请求示例

```http
GET /api/v1/admin/offline-vips/12
Authorization: Bearer <access_token>
```

非法示例（`vip_id` 非整数 → 422）：

```http
GET /api/v1/admin/offline-vips/abc
```

#### 返回参数

同 `OfflineVipItem`（见 2.1）。

#### 返回示例

```json
{ "id": 12, "user_id": 396140, "member_code": "G396140", "progress": "dating", "progress_label": "约会进行中", "contract_amount": "19800.00" }
```

空数据说明：记录不存在时返回 404，**不返回空对象**。

#### 使用方法与业务规则

- 仅返回未软删记录；`deleted_at` 非空视为不存在。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无读权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 404 | 记录不存在或已删除 | toast + 刷新列表 | `{"detail":"线下VIP记录不存在"}` |
| 422 | `vip_id` 非整数 | — | `{"detail":[{"loc":["path","vip_id"],"msg":"Input should be a valid integer"}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含非法示例）
- [x] 返回参数表（引用 2.1）
- [x] 返回示例
- [x] 使用方法与业务规则
- [x] 错误表

---

## 7. 编辑

### 7.1 `PUT /api/v1/admin/offline-vips/{vip_id}`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips/{vip_id}` |
| 方法 | `PUT` |
| 权限 | `matchmaker.member.manage` |
| 用途 | 编辑线下VIP会员服务信息；改动成功约见次数时写人工修改记录 |
| 请求体 | `application/json` |
| 返回 | `OfflineVipItem` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 校验 | 业务含义 |
|---|---|---|---|---|---|
| `vip_id` | path | int | 是 | `ge=1` | 记录 ID |
| `sales_matchmaker_id` | body | int \| null | 否 | `ge=1` | 销售红娘 |
| `service_matchmaker_id` | body | int \| null | 否 | `ge=1` | 服务红娘 |
| `promoter_id` | body | int \| null | 否 | `ge=1` | 推广红娘 |
| `sign_date` | body | date \| null | 否 | — | 签约日期 |
| `service_start` | body | date \| null | 否 | — | 服务开始 |
| `service_end` | body | date \| null | 否 | — | 服务结束 |
| `package_name` | body | string \| null | 否 | `≤128` | 服务套餐 |
| `contract_amount` | body | decimal \| null | 否 | `0 ~ 100000000` | 合同金额 |
| `promise_meet_count` | body | int \| null | 否 | `0 ~ 100000` | 承诺约见人数 |
| `success_meet_count` | body | int \| null | 否 | `0 ~ 100000` | 已成功约见人数（变化时写记录） |
| `progress` | body | string \| null | 否 | 枚举（见 1.2） | 服务进度 |
| `remark` | body | string \| null | 否 | `≤500` | 备注信息 |
| `attach_urls` | body | string[] \| null | 否 | — | 图片附件 |
| `meet_change_remark` | body | string \| null | 否 | `≤255` | 人工修改成功约见次数的说明（**仅用于写记录，不落 `offline_vip`**） |

> **语义**：字段「不传」= 不修改；传 `null` = 置空（字符串/日期类字段）。采用 `exclude_unset` 语义。

#### 请求示例

合法：

```json
{
  "progress": "met_parents",
  "success_meet_count": 3,
  "meet_change_remark": "线下补录两次成功约见",
  "remark": "已见父母"
}
```

非法示例（进度枚举非法 → 422）：

```json
{ "progress": "unknown" }
```

非法示例（成功约见人数超上限 → 422）：

```json
{ "success_meet_count": 999999 }
```

#### 返回参数

同 `OfflineVipItem`（见 2.1）。

#### 返回示例

```json
{ "id": 12, "progress": "met_parents", "progress_label": "已见父母", "success_meet_count": 3, "contract_amount": "19800.00" }
```

#### 使用方法与业务规则

- 仅更新显式传入的字段；每次调用写一条 `business_audit_log`（`action='offline_vip.update'`）。
- `success_meet_count` 与库中当前值**不同**时，额外写一条 `offline_vip_meet_log`：`before_count` / `after_count` / `remark=meet_change_remark` / `changed_by=当前管理员`。相同则不写记录。
- `progress` 变更不影响其他字段。
- 记录不存在或已软删 → 404。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无写权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 404 | 记录不存在 | toast + 刷新 | `{"detail":"线下VIP记录不存在"}` |
| 422 | 枚举非法 / 数值越界 / `remark` 超长 | 校正表单 | `{"detail":[{"loc":["body","progress"],"msg":"Input should be 'matching', 'dating', ..."}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（引用 2.1）
- [x] 返回示例
- [x] 使用方法与业务规则（含人工修改记录规则）
- [x] 错误表

---

## 8. 成功约见修改记录

### 8.1 `GET /api/v1/admin/offline-vips/{vip_id}/meet-logs`

#### 基本信息

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/offline-vips/{vip_id}/meet-logs` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` |
| 用途 | 查询该线下VIP「成功约见次数」的人工修改历史 |
| 返回 | `OfflineVipMeetLogPage` |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `vip_id` | path | int | 是 | — | `ge=1` | 记录 ID |
| `page` | query | int | 否 | `1` | `ge=1, le=1000` | 页码 |
| `page_size` | query | int | 否 | `20` | `ge=1, le=100` | 每页条数 |

#### 请求示例

```http
GET /api/v1/admin/offline-vips/12/meet-logs?page=1&page_size=20
Authorization: Bearer <access_token>
```

非法示例（`vip_id` 为 0 → 422）：

```http
GET /api/v1/admin/offline-vips/0/meet-logs
```

#### 返回参数

`OfflineVipMeetLogPage`：`items / page / page_size / total / has_more`。

`OfflineVipMeetLogItem`：

| 字段 | 类型 | 可为空 | 业务含义 |
|---|---|---|---|
| `id` | int | 否 | 记录 ID |
| `vip_id` | int | 否 | 关联 `offline_vip.id` |
| `before_count` | int | 否 | 修改前成功约见数 |
| `after_count` | int | 否 | 修改后成功约见数 |
| `remark` | string | 是 | 修改说明 |
| `changed_by` | int | 是 | 修改人（后台账号 ID） |
| `changed_by_name` | string | 是 | 修改人称呼（`matchmaker_admin_account.display_name` 优先，回退 `users.nickname`，再回退 `admin`） |
| `created_at` | datetime | 是 | 修改时间 |

#### 返回示例

```json
{
  "items": [
    {
      "id": 5,
      "vip_id": 12,
      "before_count": 1,
      "after_count": 3,
      "remark": "线下补录两次成功约见",
      "changed_by": 7,
      "changed_by_name": "芸希老师",
      "created_at": "2026-09-11T15:02:00"
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

- 排序 `id DESC`（最新在前）。
- 调用前会先校验 `vip_id` 对应记录存在，不存在 → 404。
- 仅记录**人工修改**（`PUT` 提交且数值变化），系统自动累加不写此表。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 |
|---|---|---|---|
| 401 | 未登录 | 自动跳登录 | `{"detail":"请先登录红娘后台"}` |
| 403 | 无读权限 | 展示「无权限」 | `{"detail":"无权限访问"}` |
| 404 | 记录不存在 | toast + 刷新 | `{"detail":"线下VIP记录不存在"}` |
| 422 | `vip_id`/分页参数越界 | — | `{"detail":[{"loc":["path","vip_id"],"msg":"Input should be greater than or equal to 1"}]}` |

#### 文档完成自检清单

- [x] 基本信息
- [x] 请求参数表
- [x] 请求示例（含非法示例）
- [x] 返回参数表（逐字段业务含义）
- [x] 返回示例（含空数据示例）
- [x] 使用方法与业务规则
- [x] 错误表

---

## 9. 总览：本模块全部端点

| 方法 | 路径 | 权限 | 用途 | 返回 |
|---|---|---|---|---|
| GET | `/api/v1/admin/offline-vips` | `matchmaker.member.read` | 列表分页 | `OfflineVipPage` |
| GET | `/api/v1/admin/offline-vips/statistics` | `matchmaker.member.read` | 10 张统计卡 | `OfflineVipStatistics` |
| GET | `/api/v1/admin/offline-vips/options` | `matchmaker.member.read` | 下拉选项 | `OfflineVipOptions` |
| POST | `/api/v1/admin/offline-vips` | `matchmaker.member.manage` | 新增 | `OfflineVipItem`（201） |
| GET | `/api/v1/admin/offline-vips/{vip_id}` | `matchmaker.member.read` | 详情 | `OfflineVipItem` |
| PUT | `/api/v1/admin/offline-vips/{vip_id}` | `matchmaker.member.manage` | 编辑 | `OfflineVipItem` |
| GET | `/api/v1/admin/offline-vips/{vip_id}/meet-logs` | `matchmaker.member.read` | 成功约见修改记录 | `OfflineVipMeetLogPage` |

> 静态子路径（`/statistics`、`/options`）声明在 `/{vip_id}` 之前，避免被路径参数吃掉。

### 9.1 前端尚未接线的 UI 交互（需产品确认）

| UI 元素 | 现状 | 说明 |
|---|---|---|
| 顶部 Tab「约会管理」「合同管理」 | 静态 UI 无对应面板 | **未新增页面**；`offline_vip` 已预留 `contract_*` 字段供后续接入 |
| 「业绩报表」按钮 | 静态 UI 无对应页面 | 未实现，未新增 UI |
| 「套餐管理」按钮 | 静态 UI 无对应弹窗 | 未实现，未新增 UI；套餐类型选项由 `/options` 提供 |
| 「设置」（子 Tab 右侧齿轮） | 静态 UI 无对应弹窗 | 未实现，未新增 UI |
| 表格行操作 | 静态 UI 表格无「操作」列、无行按钮 | **未新增操作列**；改为点击「会员」单元格打开编辑抽屉 |
| 服务进度流转 | 静态 UI 抽屉内无进度选择控件 | 后端 `PUT` 已支持 `progress`，**前端未新增控件**；如需在页面流转请确认设计后补充 |

### 9.2 数据库

本模块使用 `database_setup_marriage.py` 中已存在的两张表，**本次未新增表**：

- `offline_vip`（线下VIP会员服务记录）
- `offline_vip_meet_log`（成功约见次数人工修改记录）

---

## 10. 变更记录

| 日期 | 变更 | 变更前 | 变更后 | 影响范围 |
|---|---|---|---|---|
| 2026-09-11 | 新建模块 | 无 `offline_vip` 相关管理端接口 | 新增 7 个端点 + `offline_vip_admin` 三件套 | 会员CRM → 线下VIP 页面 |
| 2026-09-11 | 权限映射 | `_matchmaker_admin_permission` 无 `/offline-vips` 规则 | 新增 `/offline-vips` → `matchmaker.member.read/manage` | 权限校验 |
