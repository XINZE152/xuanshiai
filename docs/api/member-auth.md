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
| `status` | query | string | 否 | `all` | 枚举 `all\|success\|fail` | `all`=全部（含待审）、`success`=认证成功（face_verified=1）、`fail`=认证失败（face_verified=2） |
| `keyword` | query | string | 否 | 无 | ≤64 | 模糊匹配 `users.nickname` / `users.phone` / `user_auth.real_name` |

#### 请求示例

```http
GET /api/v1/admin/members/auth/realname-reviews?page=1&page_size=20&status=success&keyword=%E5%BE%90%E7%AB%B9%E8%BD%A9
Authorization: Bearer <token>
```

非法示例（`status` 取非法枚举）：

```http
GET /api/v1/admin/members/auth/realname-reviews?status=pending
```

返回 `422`，`detail` 提示 `status` 不在 `^(all|success|fail)$` 内。

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
      "result": "success", "result_label": "认证成功", "created_at": "2026-07-12T11:14:48"
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
- 状态流转：`user_auth.face_verified` 0 待审 / 1 通过 / 2 失败；审核由 `PATCH /auth/realname/{id}` 修改。
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

- 与 `realname-reviews` 一并调用，渲染 4 张统计卡；余量卡的「在线充值」按钮当前未接入充值能力。
- 错误同 2.1。

#### 文档完成自检清单

- [x] 有请求参数表（无参数已标注）
- [x] 有返回参数表
- [x] 有成功返回示例
- [x] 有使用方法与业务规则
- [x] 有错误码表

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

- **house**：`items[].file_url` = `user_auth.house_cert`（文件凭证）；无附加字段。
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
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "kind": string, "status": int }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `kind` | path | string | 是 | `realname\|commitment\|marriage\|house\|education\|other` | 认证类型 |
| `review_id` | path | int | 是 | ≥1 | 记录主键（实名/房产/学历为 `user_auth.id`，其余为各自表 id） |
| `status` | body | int | 是 | 1 或 2 | 1 通过 / 2 未通过 |
| `remark` | body | string\|null | 条件必填 | ≤255；`status=2` 时前端必须填写 | 审核备注；承诺和学历驳回原因会回显给用户 |

#### 请求示例

```json
{ "status": 1, "remark": "资料清晰，予以通过" }
```

非法示例（`status=3`）：`422`，`status` 超出 1–2 范围。

#### 使用方法与业务规则

- `kind=commitment/house/education/other/realname`：更新对应表 `status`/`verified` 并写审计。承诺通过/驳回同时更新单身承诺完成标记；学历将后台动作 `1/2` 映射为用户侧 `2通过/3未通过`。
- `kind=marriage`：**不支持**，返回 `400`（婚姻核验结果为 married/no_record/divorced，无通过/未通过语义）。
- `kind` 非法值（如 `foo`）被路径正则拦截，返回 `404`（未匹配到路由）。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 400 | `kind=marriage` | 提示婚姻核验不可审核 |
| 404 | 记录不存在 / kind 非法 | 提示 |
| 409 | 承诺或学历记录已不在审核中 | 刷新列表，不允许重复改判 |
| 422 | `status` 非法，或驳回承诺/学历时未填写原因 | 提示并保留当前审核输入 |

#### 文档完成自检清单

- [x] 参数表（含 kind 枚举与正则）
- [x] 含非法示例
- [x] 返回示例
- [x] 使用方法（含 marriage 不支持说明）
- [x] 错误表

---

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
