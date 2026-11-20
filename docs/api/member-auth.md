# 会员认证（M3-1）管理后台接口

## 1. 通用约定

接口前缀：`/api/v1`。

需要登录的接口必须携带：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

成功响应**没有**统一 `data` 包装层，返回体就是接口定义的对象或数组（分页接口返回 `items / page / page_size / total / has_more`）。
本模块所有写接口写入 `business_audit_log`（actor_user_id / action / resource_type / resource_id / after_json），action 形如 `member.auth.{kind}.review`。
时间戳由 MySQL 写 `UTC_TIMESTAMP()`，后端不自行生成；返回时间字段为 `datetime`（ISO 字符串）。
金额类字段（`face_score`、`cost`、`realname_fee`）一律序列化为**字符串**，前端按字符串处理。
会员编号 `member_code` 由后端 `CONCAT('G', LPAD(u.id, 6, '0'))` 生成，前端直接使用，无需本地拼装。

权限：读接口需 `matchmaker.member.read`，写接口需 `matchmaker.member.manage`（依赖注入按路径 `/admin/members` 自动映射，缺权限返回 `403`）。

---

## 2. 实名认证

### 2.1 `GET /api/v1/admin/members/auth/realname-reviews`

#### 基本信息

- 用途：分页查询会员实名认证审核列表（含待审/认证成功/认证失败）。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | ≥1, ≤1000 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1, ≤100 | 每页条数 |
| `status` | query | string | 否 | `all` | 枚举 `all\|pending\|success\|fail` | `all`=已提交的实名记录；`pending`=审核中/复核（1/4），`success`=通过（2），`fail`=未通过/撤销（3/5） |
| `keyword` | query | string | 否 | 无 | ≤64 | 模糊匹配 `users.nickname` / `users.phone` / `user_auth.real_name` |

#### 请求示例

```http
GET /api/v1/admin/members/auth/realname-reviews?page=1&page_size=20&status=success&keyword=%E5%BE%90%E7%AB%B9%E8%BD%A9
Authorization: Bearer <token>
```

非法示例（`status` 取非法枚举）：

```http
GET /api/v1/admin/members/auth/realname-reviews?status=unknown
```

返回 `422`，`detail` 提示 `status` 不在 `^(all|pending|success|fail)$` 内。

#### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | 列表 |
| `items[].id` | int | 是 | — | 审核记录主键（= `user_auth.id`） |
| `items[].user_id` | int | 是 | — | 会员 user_id，「查看资料」使用 |
| `items[].member_code` | string | 是 | — | 会员编号 `G`+6 位 |
| `items[].nickname` | string\|null | 是 | `null` | 昵称 |
| `items[].avatar` | string\|null | 是 | `null` | 头像 URL |
| `items[].real_name` | string\|null | 是 | `null` | 实名姓名 |
| `items[].id_card_masked` | string\|null | 是 | `null` | 身份证掩码（保留前 12 位 + `******`） |
| `items[].gender` | string\|null | 是 | `null` | 性别：男/女 |
| `items[].birthday` | string\|null | 是 | `null` | 出生日期 `YYYY-MM-DD` |
| `items[].id_card_issued` | string\|null | 是 | `null` | 发证机关 |
| `items[].id_card_front` | string\|null | 是 | `null` | 身份证正面 URL |
| `items[].id_card_back` | string\|null | 是 | `null` | 身份证反面 URL |
| `items[].face_method` | string\|null | 是 | `null` | 验证方式：动作活检/照片比对 |
| `items[].face_vendor` | string\|null | 是 | `null` | 人脸服务商 |
| `items[].face_score` | string\|null | 是 | `null` | 人脸比对得分（字符串） |
| `items[].face_photo` | string\|null | 是 | `null` | 人脸核验文件 URL |
| `items[].file_url` | string\|null | 是 | `null` | 同 `face_photo`（核验文件） |
| `items[].result` | string | 是 | — | `success`/`fail`/`pending` |
| `items[].result_label` | string | 是 | — | 认证成功/认证失败/待审 |
| `items[].submission_version` | int | 是 | — | 本次提交版本；审核时原样传回 |
| `items[].created_at` | string\|null | 是 | `null` | 提交认证时间 |
| `page` / `page_size` / `total` / `has_more` | int/bool | 是 | — | 分页元数据 |

#### 返回示例

```json
{
  "items": [
    {
      "id": 364, "user_id": 364, "member_code": "G000364", "nickname": "hunyun",
      "avatar": null, "real_name": "王宇琪", "id_card_masked": "330105199306******",
      "gender": "男", "birthday": "1993-06-15", "id_card_issued": "浙江省杭州市",
      "id_card_front": "/uploads/id/1.jpg", "id_card_back": "/uploads/id/2.jpg",
      "face_method": "动作活检", "face_vendor": "腾讯云人脸核身", "face_score": "93.39",
      "face_photo": "/uploads/face/1.jpg", "file_url": "/uploads/face/1.jpg",
      "result": "success", "result_label": "认证成功", "created_at": "2026-07-12T11:14:48", "submission_version": 1
    }
  ],
  "page": 1, "page_size": 20, "total": 1, "has_more": false
}
```

空数据示例：

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

#### 使用方法与业务规则

- 前置：登录且具备 `matchmaker.member.read`。
- 调用顺序：页面「实名认证」Tab 进入时调用本接口 + `realname-stats`；切换状态 Tab / 点击「搜索」时带新 `status`/`keyword` 重新调用。
- **任务 15 变更**：列表从按 `face_verified` 分类改为按 `realname_status` 分类；审核使用 §7 并回传 `submission_version`。§2.2 仍是人脸核验统计，故与实名列表数不必相同。
- 边界：无权限返回 `403`；未登录 `401`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录/令牌失效 | 跳转登录 |
| 403 | 无 `matchmaker.member.read` | 提示无权限 |
| 422 | `status` 非枚举值 | 修正参数 |

#### 文档完成自检清单

- [x] 有请求参数表，且每个参数都写了业务含义
- [x] 有完整请求体示例（含非法示例）
- [x] 有返回参数表，每个字段都写了业务含义
- [x] 有成功返回示例（含空数据示例）
- [x] 有使用方法与业务规则小节
- [x] 有错误码表
- [x] 至少一个非法参数示例

---

### 2.2 `GET /api/v1/admin/members/auth/realname-stats`

#### 基本信息

- 用途：实名认证统计卡：人脸核验余量 / 核验成功 / 核验失败 / 总计消耗。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。无请求体。

#### 请求参数

无。

#### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `quota_remaining` | int | 是 | 0 | 人脸核验余量（当前无独立额度数据源，恒为 0） |
| `success_count` | int | 是 | 0 | 核验成功次数（face_verified=1） |
| `fail_count` | int | 是 | 0 | 核验失败次数（face_verified=2） |
| `total_consumed` | int | 是 | 0 | 总计消耗 = success + fail |

#### 返回示例

```json
{ "quota_remaining": 0, "success_count": 361, "fail_count": 9, "total_consumed": 370 }
```

#### 使用方法与业务规则

- 与 `realname-reviews` 一并调用，渲染 4 张统计卡；余量卡的「在线充值」按钮对应 §2.3 核验额度充值接（未接入时 `quota-packages.recharge_enabled=false`，前端保持「展示余量 + 联系服务商」弹窗）。
- 错误同 2.1。

#### 文档完成自检清单

- [x] 有请求参数表（无参数已标注）
- [x] 有返回参数表
- [x] 有成功返回示例
- [x] 有使用方法与业务规则
- [x] 有错误码表

---

### 2.3 核验额度在线充值（A17，新增 2026-10-24）

实名认证（人脸核验）与婚姻状况核验按第三方厂商用量计费，需先充值额度。本阶段后端只落「充值订单」骨架，真实额度扣减/结算由厂商回调驱动（尚未接线）；`provider` 由配置 `verification_recharge_provider` 控制，三档：`disabled`（默认，厂商未定）、`mock`（仅开发/联调，配置校验禁止出现在 staging/production）、`real`（预留，未接入时下单返回 501）。

#### `GET /api/v1/admin/members/auth/quota-packages`

- 权限：`matchmaker.member.read`。成功 `200`。无请求体。
- 请求参数：`kind`（query，可选，`realname|marriage`；不传返回全部）。
- 返回 `QuotaPackageList`：`packages[]`（每项 `id/kind/name/quantity/price/currency/enabled`）、`provider`、`recharge_enabled`（=`provider≠disabled`）。
- **业务规则**：套餐目录为只读，**不受 `provider` 开关影响**（`disabled` 下仍可展示档位，仅 `recharge_enabled=false` 告知前端不可下单）。

#### `POST /api/v1/admin/members/auth/recharge`

- 权限：`matchmaker.member.manage`。成功 `201`，返回 `RechargeOrder`。属敏感写操作，必须同时携带 `Idempotency-Key`（8–128）与 `?confirm=true`；缺 `confirm` 返回 `428`。同键重放返回首次订单，不重复下单。
- 请求体 `RechargeCreateRequest`：`kind`（`realname|marriage`）、`package_id`（1–64）、`quantity`（份数，默认 1，范围 1–999）。
- 返回 `RechargeOrder`：`order_no/kind/package_id/package_name/quantity/unit_price/total_amount/currency/status/provider/pay_url/qr_code_url/created_at/paid_at`；`status` 为 `pending|paid|failed|cancelled`。
- **业务规则**：`provider=disabled` 下单返回 `503`（不伪造支付）；`mock` 生成待付订单 + 模拟 `pay_url`；`real` 未接入返回 `501`；订单表未建（未执行迁移）返回 `503`。写审计 `action='member.auth.recharge'`。

#### `GET /api/v1/admin/members/auth/recharge/{order_no}`

- 权限：`matchmaker.member.read`。成功 `200`，返回 `RechargeOrder`。`order_no` 为 path 参数（1–64）。按订单号查本地状态（支付回调后刷新）；订单不存在 `404`；表未就绪 `503`。

#### `POST /api/v1/admin/members/auth/recharge/{order_no}/callback`

- 权限：`matchmaker.member.manage`。成功 `200`，返回更新后的 `RechargeOrder`。
- 请求体 `{"status": "paid"|"failed"}`（默认 `paid`）。**仅供 `mock` provider 手动联调**：将 `pending` 订单置为 `paid/failed`；非 mock 环境调用返回 `503`；对非 mock 下单的订单手动回调返回 `409`。`real` 由厂商签名回调驱动，不走本接口。

#### 错误（本小节通用）

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401/403 | 未登录/无权限 | 跳转或提示 |
| 404 | 订单不存在 | 提示 |
| 409 | 非 mock 订单手动回调 | 拒绝 |
| 422 | 参数非法（kind/package_id/quantity） | 提示具体字段 |
| 428 | 下单缺 `confirm=true` | 要求二次确认 |
| 501 | `real` provider 尚未接入 | 提示厂商未接入 |
| 503 | `disabled` 下单 / 非 mock 回调 / 订单表未就绪 | 保持展示余量弹窗，不伪造调用 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回字段含义
- [x] 使用方法（含 provider 三档行为）
- [x] 错误码表（含 503/501）

---

## 3. 会员承诺

### 3.1 `GET /api/v1/admin/members/auth/commitment-reviews`

#### 基本信息

- 用途：分页查询会员承诺书签署记录。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | ≥1 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1 | 每页条数 |
| `status` | query | string | 否 | `all` | `all\|pass\|pending\|fail` | `pass`=通过(1) / `pending`=待审(0) / `fail`=未通过(2) |
| `keyword` | query | string | 否 | 无 | ≤64 | 匹配昵称/手机号/实名姓名 |

#### 请求示例

```http
GET /api/v1/admin/members/auth/commitment-reviews?status=pending&keyword=%E5%BE%90
```

非法示例：`status=ok` → `422`。

#### 返回参数（仅列扩展字段，公共字段同 2.1）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `items[].sign_times` | int | 第几次签署 |
| `items[].title` | string\|null | 承诺书标题 |
| `items[].file_url` | string\|null | 签名文件 URL |

#### 返回示例（含空数据）

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

#### 使用方法与业务规则

- 列表「签署结果」列：仅 `pending` 渲染审核下拉，`pass`/`fail` 渲染只读徽标；切换时调 `PATCH /auth/commitment/{id}`（见 §7）。驳回必须填写 `remark`，该原因会回显给用户；用户重新签署后会生成一条新的待审记录。
- 审核通过会同步 `users.is_single_pledge=1`；驳回会同步为 `0`。单纯存在签署记录不等于认证通过。
- 删除调 `DELETE /auth/commitment/{id}`（见 §10）。

#### 错误

同 2.1。

#### 文档完成自检清单

- [x] 参数表完整
- [x] 含非法示例
- [x] 含空数据示例
- [x] 返回字段业务含义
- [x] 使用方法与错误表

---

## 4. 婚姻状况

### 4.1 `GET /api/v1/admin/members/auth/marriage-reviews`

#### 基本信息

- 用途：分页查询婚姻状况核验记录。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | ≥1 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1 | 每页条数 |
| `status` | query | string | 否 | `all` | `all\|married\|no_record\|divorced` | 核验结果枚举 |
| `keyword` | query | string | 否 | 无 | ≤64 | 匹配昵称/手机号/实名姓名 |

#### 返回参数（扩展字段）

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `items[].check_method` | string\|null | 核验方式（民政数据接口/线下核查） |
| `items[].declared_status` | string\|null | 客户资料中填写的婚姻状态 |
| `items[].result` | string | `married`/`no_record`/`divorced` |
| `items[].result_label` | string | 已婚/无登记信息/离异 |
| `items[].cost` | string\|null | 查询费用（字符串） |
| `items[].checked_at` | string\|null | 核验时间 |

#### 返回示例

```json
{
  "items": [
    { "id": 1, "user_id": 9, "member_code": "G000009", "nickname": "Sofia",
      "real_name": "陈林林", "id_card_masked": "420381200012******",
      "check_method": "民政数据接口", "declared_status": "未婚", "result": "married",
      "result_label": "已婚", "cost": "0.00", "checked_at": "2026-07-01T10:00:00",
      "created_at": "2026-07-01T10:00:00" }
  ],
  "page": 1, "page_size": 20, "total": 1, "has_more": false
}
```

#### 使用方法与业务规则

- 婚姻核验结果本身为 `married/no_record/divorced`，**不支持通过/未通过审核**，仅可查看或删除（`DELETE /auth/marriage/{id}`）。
- `cost` 当前多为 `0.00`，无数据时可展示「暂无数据」。

#### 错误

同 2.1。

#### 文档完成自检清单

- [x] 参数表完整
- [x] 含非法示例（`status` 非枚举）
- [x] 返回字段含义
- [x] 使用方法与错误表

---

### 4.2 `GET /api/v1/admin/members/auth/marriage-stats`

同 §2.2，返回 `{ quota_remaining: int, total_consumed: int }`，`total_consumed` = `user_marriage_check` 记录数。

---

## 5. 房产 / 学历 / 其他认证

### 5.1 `GET /api/v1/admin/members/auth/house-reviews`

### 5.2 `GET /api/v1/admin/members/auth/education-reviews`

### 5.3 `GET /api/v1/admin/members/auth/other-reviews`

三者结构一致，差异见下表。权限均为 `matchmaker.member.read`。

#### 请求参数（通用）

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | ≥1 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1 | 每页条数 |
| `status` | query | string | 否 | `all` | `all\|pass\|pending\|fail` | 审核状态 |
| `keyword` | query | string | 否 | 无 | ≤64 | 匹配昵称/手机号/实名姓名 |
| `auth_type_id` | query | int | 否（仅 other） | 无 | ≥1 | 其他认证：按认证类型过滤 |

#### 返回参数差异

- **house**：`items[].file_url` = `user_auth.house_cert`（文件凭证）；底层四态为 0未提交、1审核中、2通过、3未通过，并映射为 `pending/pass/fail`。
- **education**：只返回 `education_cert IS NOT NULL` 的已提交记录；`items[].degree`（学历）、`items[].school`（毕业学校）；`file_url` = `education_cert`。底层四态为 0未提交、1审核中、2通过、3未通过，并映射为 `pending/pass/fail`。
- **other**：`items[].auth_type_id`（int）、`items[].auth_type_name`（string）；`file_url` = `user_auth_extra.file_url`。

#### 返回示例（education）

```json
{
  "items": [
    { "id": 248, "user_id": 364, "member_code": "G000364", "nickname": "Ellen",
      "real_name": "徐竹轩", "id_card_masked": "320582199306******",
      "file_url": "/uploads/edu/1.jpg", "result": "pass", "result_label": "通过",
      "degree": "博士", "school": "罗格斯大学", "created_at": "2026-07-05T22:41:13" }
  ],
  "page": 1, "page_size": 20, "total": 1, "has_more": false
}
```

空数据：

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

#### 使用方法与业务规则

- 学历列表的文件按钮打开证明图片；仅 `pending` 可通过下拉审核，已通过和已驳回记录只读。驳回必须填写 `remark`，该原因写入 `education_fail_reason` 并回显给用户；用户重新提交后才会再次进入 `pending`。
- 「删除」调对应 `DELETE /auth/{kind}/{id}`。
- other 的「认证类型」下拉选项来自 `GET /auth/types`（见 §6），选中后带 `auth_type_id` 重查。

#### 错误

同 2.1。

#### 文档完成自检清单（三项共通）

- [x] 参数表完整（含 other 的 auth_type_id）
- [x] 含非法示例
- [x] 返回字段含义
- [x] 含空数据示例
- [x] 使用方法与错误表

---

## 6. 认证类型（其他认证）

### 6.1 `GET /api/v1/admin/members/auth/types`

#### 基本信息

- 用途：返回全部认证类型（**纯数组，非分页**），供「其他认证」筛选下拉与「管理认证类型」抽屉使用。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。无请求体。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `keyword` | query | string | 否 | 无 | ≤64 | 按 `name` 模糊匹配 |

#### 返回参数（数组元素 `AuthTypeItem`）

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `id` | int | 是 | 类型主键 |
| `name` | string | 是 | 类型名称（唯一） |
| `icon_url` | string\|null | 是 | 图标 URL |
| `description` | string\|null | 是 | 说明文案 |
| `require_realname` | bool | 是 | 提交前是否要求先实名 |
| `sort` | int | 是 | 显示排序（大靠前） |
| `status` | int | 是 | 1 启用 / 0 关闭 |
| `created_at` / `updated_at` | string\|null | 是 | 时间戳 |

#### 返回示例

```json
[
  { "id": 1, "name": "收入证明", "icon_url": "/uploads/icon/1.png", "description": "上传收入流水",
    "require_realname": true, "sort": 0, "status": 1, "created_at": "2026-08-01T00:00:00", "updated_at": "2026-08-01T00:00:00" }
]
```

#### 文档完成自检清单

- [x] 参数表
- [x] 返回字段含义
- [x] 成功示例
- [x] 使用方法与错误表

---

### 6.2 `POST /api/v1/admin/members/auth/types`

#### 基本信息

- 用途：创建认证类型。
- 权限：`matchmaker.member.manage`。
- 成功状态：`201 Created`。

#### 请求参数（body `AuthTypeCreate`）

| 字段 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `name` | body | string | 是 | 1–64 | 类型名称（唯一） |
| `icon_url` | body | string\|null | 否 | ≤512 | 图标 URL（先经 `POST /admin/common/upload` 上传） |
| `description` | body | string\|null | 否 | ≤1000 | 说明文案 |
| `require_realname` | body | bool | 否 | 默认 true | 是否要求先实名 |
| `sort` | body | int | 否 | ≥0，默认 0 | 排序 |
| `status` | body | int | 否 | 0/1，默认 1 | 1 启用 / 0 关闭 |

#### 请求示例

```json
{ "name": "收入证明", "icon_url": "/uploads/icon/1.png", "require_realname": true, "sort": 0, "status": 1 }
```

非法示例（`name` 超长）：

```json
{ "name": "这是超过六十四个字符的非常非常长的认证类型名称用来触发校验错误xxxxxxxxxxxxxxxx" }
```

→ `422`，`name` 超过 64 字符。

#### 返回参数

返回创建的 `AuthTypeItem`（同 §6.1）。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 409 | `name` 已存在 | 提示「认证类型名称已存在」 |
| 422 | 字段校验失败 | 提示具体字段 |
| 401/403 | 未登录/无权限 | 跳转或提示 |

#### 文档完成自检清单

- [x] 参数表（含校验）
- [x] 含非法示例
- [x] 返回字段含义
- [x] 成功示例
- [x] 错误表

---

### 6.3 `PUT /api/v1/admin/members/auth/types/{type_id}`

#### 基本信息

- 用途：更新认证类型（全字段可选）。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `type_id` | path | int | 是 | ≥1 | 类型主键 |
| body | body | `AuthTypeUpdate` | 是 | 全字段可选，同 `AuthTypeCreate` 但不强制 | 仅传需变更字段 |

#### 请求示例

```json
{ "status": 0 }
```

非法示例（`status=2`）：`422`。

#### 返回参数

返回更新后的 `AuthTypeItem`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | `type_id` 不存在 | 提示不存在 |
| 409 | 修改后的 `name` 与他人冲突 | 提示名称已存在 |
| 422 | 字段校验失败 | 提示具体字段 |

#### 文档完成自检清单

- [x] 参数表
- [x] 含非法示例
- [x] 返回字段含义
- [x] 错误表

---

### 6.4 `DELETE /api/v1/admin/members/auth/types/{type_id}`

#### 基本信息

- 用途：删除认证类型（物理删除）。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "deleted": true }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- | --- |
| `type_id` | path | int | 是 | 类型主键 |

#### 返回示例

```json
{ "id": 1, "deleted": true }
```

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 类型不存在 | 提示 |
| 409 | 已被 `user_auth_extra` 引用 | 提示「该认证类型已被会员提交记录引用，无法删除」 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回示例
- [x] 错误表（含 409 引用冲突）

---

## 7-8. 审核 / 删除（动态路由）

### 7. `PATCH /api/v1/admin/members/auth/{kind}/{review_id}`

#### 基本信息

- 用途：对认证记录执行通过(1)/未通过(2)审核。
- 权限：后台登录，`Authorization: Bearer <token>`，需要 `matchmaker.member.manage`。
- 无 query；请求和响应 Content-Type 均为 `application/json`。成功为 `200 OK`，返回 `{ "id": int, "kind": string, "status": int }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `kind` | path | string | 是 | `realname\|commitment\|marriage\|house\|education\|other` | 认证类型 |
| `review_id` | path | int | 是 | ≥1 | 记录主键（实名/房产/学历为 `user_auth.id`，其余为各自表 id） |
| `status` | body | int | 是 | 1 或 2 | 1 通过 / 2 未通过 |
| `remark` | body | string\|null | 承诺、学历驳回必填；实名可省略，默认 `null` | ≤255；实名驳回如传字符串，不得全空白 | 新客户端实名驳回应传原因代码（见下文）；旧请求省略或传自由备注时只使用固定通用原因 |
| `submission_version` | body | int\|null | 实名重提后必填，默认 `null` | ≥0 | 原样回传审核列表返回的 `submission_version`；其他认证忽略 |

#### 请求示例

```http
PATCH /api/v1/admin/members/auth/realname/364
Authorization: Bearer <token>
Content-Type: application/json

{"status":2,"remark":"document_unclear","submission_version":2}
```

`200 application/json`：`{"id":364,"kind":"realname","status":2}`。`id`（整数）为认证记录主键；`kind`（字符串）原样返回类别；`status`（整数）回显动作（1 通过、2 未通过），三项均必返且不为空。非法示例：`{"status":2,"remark":"  "}` → `422 {"detail":"审核未通过时请填写有效原因或省略该字段"}`；`status=3` → `422 {"detail":"实名认证请求参数无效"}`（仅示例中的实名路径）。

#### 使用方法与业务规则

- `kind=commitment/house/education/other/realname`：更新对应表状态并写审计；审核动作 `status=1/2` 分别表示通过/未通过。承诺通过/驳回同时更新单身承诺完成标记；学历和房产将后台动作 `1/2` 映射为用户侧 `2通过/3未通过`。实名仅接受 `realname_status=1/4` 且具有实名材料的记录；房产仅接受凭证存在且 `house_verified=1` 的待审记录；其他认证仅接受 `status=0` 的待审记录，重复或过期审核返回 `409`。房产提交时进入 `house_verified=1` 待审状态。
- `kind=marriage`：**不支持**，返回 `400`（婚姻核验结果为 married/no_record/divorced，无通过/未通过语义）。
- `kind` 非法值（如 `foo`）被路径参数校验拦截，返回 `422`。实名审核没有专用次数限制，不要求 `Idempotency-Key`；并发或重放由状态和提交版本拦截。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 400 | `kind=marriage` | 提示婚姻核验不可审核 |
| 401/403 | 未登录或无审核权限 | 重新登录或核对权限 |
| 404 | 记录不存在 | 提示并刷新列表 |
| 409 | 记录已不在审核中，或实名版本不匹配/重提后未传版本，或实名通过时证件冲突 | 刷新列表并核对，不能直接重放 |
| 422 | 参数非法、实名原因全空白，或承诺/学历驳回未填写原因 | 检查参数，不回显实名请求原始输入 |
| 500/503 | 审核或通知写入失败 | 未确认结果时先刷新；未提交的事务不会留下审核结果或通知 |

#### 文档完成自检清单

- [x] 参数表（含 kind 枚举与正则）
- [x] 含非法示例
- [x] 返回示例
- [x] 使用方法（含 marriage 不支持说明）
- [x] 错误表

#### 变更记录

- **任务 15**：实名审核按当前实名状态与提交版本判定；与纯实名审核共用同一行锁，审核结果和站内通知在同一事务。重复或过期审核返回 `409`，不重复发通知。只有会员认证后台实际审核已存人脸材料时，才可同步尚未核验的人脸标记。新客户端驳回应选择 `identity_mismatch`（身份资料不一致）、`document_unclear`（证件资料不清晰）或 `photo_mismatch`（人脸与资料不一致）。服务端映射为固定文案；旧客户端省略原因或提交自由备注时，使用「实名认证未通过，请核对资料后重新提交」，不原样保存或返回自由备注。全空白字符串返回 `422`。通知不含备注、身份材料、审核人或供应商明细。旧客户端仍可审核首份资料；重提后的材料必须刷新列表并回传版本，不能不带版本盲审。
- 2026-11-18（M3-1）：房产认证列表和审核统一按用户侧四态处理；新提交的房产资料进入待审状态，审核成功/失败分别落为 2/3。审核接口增加实名、房产、其他认证的待审状态校验，避免重复审核。请求与响应结构保持兼容。

---

#### 纯实名审核入口（任务 15）

平台管理员：`GET /api/v1/admin/realname-reviews?status=1`（或 `4`），`PATCH /api/v1/admin/users/{user_id}/realname/review`。有权限且可访问该会员的红娘后台：`GET /api/v1/admin/matchmaker/members/realname-reviews?status=4`，`PATCH /api/v1/admin/matchmaker/members/{member_id}/realname/review`。这些 PATCH 使用 Bearer 登录和 JSON，以会员 ID（正整数，不是认证记录 ID）定位；成功返回 `200 application/json`。

| 字段 | 位置 | 类型 | 必填及默认 | 含义 |
| --- | --- | --- | --- | --- |
| `status` | body | int | 必填，无默认 | 2 通过、3 驳回、4 转人工复核 |
| `reason` | body | string/null | 否，默认 `null` | 最长 255 字符；实名驳回应选择固定原因代码；省略或旧自由备注按 §7 使用通用文案 |
| `submission_version` | body | int/null | 重提后必填，默认 `null` | 列表返回的提交版本，≥0 |

```http
PATCH /api/v1/admin/users/7/realname/review
Authorization: Bearer <token>
Content-Type: application/json

{"status":3,"reason":"document_unclear","submission_version":2}
```

```json
{"user_id":7,"realname_status":3,"reason":"证件资料不清晰，请核对后重新提交"}
```

三个字段均必返：`user_id`（整数）为会员 ID，`realname_status`（整数）为结果 2/3/4，`reason`（字符串或 `null`）仅驳回时返回固定公开文案。不回传原始资料、自由备注或供应商错误。非法示例 `{"status":3,"reason":"  "}` 返回 `422 {"detail":"审核未通过时请填写有效原因或省略该字段"}`；`{"status":3}` 仍兼容，使用固定通用原因。`401/403` 重新登录或核对权限和会员范围，`404` 记录不存在，`409` 应刷新后重新核对资料，`500/503` 先刷新确认是否生效。无专用限额或幂等键。此前只能由状态 1 发起审核；现在 `1→4` 可转复核，`1/4→2/3` 可终审，不重复转复核，不授予人脸通过。两个列表均新增 `items[].submission_version`；旧客户端兼容边界同 §7。

### 8. `DELETE /api/v1/admin/members/auth/{kind}/{review_id}`

#### 基本信息

- 用途：删除/重置认证记录。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "kind": string, "deleted": true }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- | --- |
| `kind` | path | string | 是 | 同 §7 |
| `review_id` | path | int | 是 | 记录主键 |

#### 使用方法与业务规则

- `commitment` / `marriage` / `other`：物理删除各自记录。
- `realname` / `house` / `education`：三者复用 `user_auth` 单行，物理删除会误删同行其它认证数据，故**改为将该认证项重置为待审（verified=0）并清空凭证文件**，并写审计（`after_json.reset=true`）。
- 前端删除前需二次确认（`window.confirm`）。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 记录不存在 | 提示 |
| 401/403 | 未登录/无权限 | 跳转或提示 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回示例
- [x] 使用方法（含重置说明）
- [x] 错误表

---

## 9. 配置域（复用 `admin/configs` 机制）

「快捷设置」「配置承诺书」「配置《婚姻状态查询授权协议》」三个抽屉复用既有配置接口，namespace 固定为 `member_auth`。

### 9.1 `GET /api/v1/admin/configs/member_auth`

- 权限：`platform.config.read`（注意：非 `matchmaker.member.read`）。
- 返回 `AdminConfigSnapshot`：`{ namespace, name, description, version, config, sensitive_keys, updated_by, updated_at }`。
- `config` 字段：`realname_force_id_card`(bool)、`realname_fee`(string)、`commitment_title`(string)、`commitment_content`(string)、`marriage_agreement`(string)。

### 9.2 `PATCH /api/v1/admin/configs/member_auth`

- 权限：`platform.config.write`。
- body（`AdminConfigUpdate`）：`{ "version": int, "config": object, "change_summary": string }`。`version` 为乐观锁，与 GET 返回的 `version` 不一致返回 `409`。
- 前端「确定提交」：合并当前 `config` 与目标键值后整体提交，例如快捷设置提交 `{ realname_force_id_card, realname_fee }`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | `member_auth` 命名空间不存在 | 由 `ensure_defaults` 自动播种，正常情况下不会出现 |
| 409 | `version` 已变化 | 重新 GET 后提交 |
| 422 | 含敏感明文 / 缺 `change_summary` | 提示 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回/请求示例
- [x] 错误表（含 409 版本冲突）

---

## 10. 总览：本模块全部端点

| Method | Path | 权限 |
| --- | --- | --- |
| GET | `/api/v1/admin/members/auth/realname-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/realname-stats` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/quota-packages` | matchmaker.member.read |
| POST | `/api/v1/admin/members/auth/recharge` | matchmaker.member.manage |
| GET | `/api/v1/admin/members/auth/recharge/{order_no}` | matchmaker.member.read |
| POST | `/api/v1/admin/members/auth/recharge/{order_no}/callback` | matchmaker.member.manage |
| GET | `/api/v1/admin/members/auth/commitment-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/marriage-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/marriage-stats` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/house-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/education-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/other-reviews` | matchmaker.member.read |
| GET | `/api/v1/admin/members/auth/types` | matchmaker.member.read |
| POST | `/api/v1/admin/members/auth/types` | matchmaker.member.manage |
| PUT | `/api/v1/admin/members/auth/types/{type_id}` | matchmaker.member.manage |
| DELETE | `/api/v1/admin/members/auth/types/{type_id}` | matchmaker.member.manage |
| PATCH | `/api/v1/admin/members/auth/{kind}/{review_id}` | matchmaker.member.manage |
| DELETE | `/api/v1/admin/members/auth/{kind}/{review_id}` | matchmaker.member.manage |
| GET | `/api/v1/admin/configs/member_auth` | platform.config.read |
| PATCH | `/api/v1/admin/configs/member_auth` | platform.config.write |

> 注意：静态路径（`/auth/realname-reviews`、`/auth/types` 等）已在路由中声明于 `/auth/{kind}/{review_id}` 之前，不会被动态路由抢占。
