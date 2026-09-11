# 会员资料媒体验证（M3-2）管理后台接口

## 1. 通用约定

接口前缀：`/api/v1`。

需要登录的接口必须携带：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

成功响应**没有**统一 `data` 包装层，返回体就是接口定义的对象或数组（分页接口返回 `items / page / page_size / total / has_more`）。
本模块所有写接口写入 `business_audit_log`（actor_user_id / action / resource_type / resource_id / after_json），action 形如 `member.media.review` / `member.media.replace` / `member.media.delete` / `member.intro.update`。
时间戳由 MySQL 写 `UTC_TIMESTAMP()`，后端不自行生成；返回时间字段为 `datetime`（ISO 字符串）。
会员编号 `member_code` 由后端 `CONCAT('G', LPAD(u.id, 6, '0'))` 生成，前端直接使用，无需本地拼装。

权限：读接口需 `matchmaker.member.read`，写接口需 `matchmaker.member.manage`（依赖注入按路径 `/admin/members` 自动映射，缺权限返回 `403`）。

媒体审核状态 `review_status` 含义：

| 值 | 含义 | 标签 |
| --- | --- | --- |
| 0 | 审核中（上传/重新上传后置位） | 审核中 |
| 1 | 已通过 | 已通过 |
| 2 | 未通过 | 未通过 |
| 3 | 已隐藏 | 已隐藏 |

> 注意：审核接口只接受 `1/2/3`（不可手动置回 `0`）。重新上传会把状态归零（待审核）。

---

## 2. 个人介绍（自白内容）

### 2.1 `GET /api/v1/admin/members/media/intros`

#### 基本信息

- 用途：分页查询会员个人介绍（自白内容），支持按英文字母/数字/中文数字特征与昵称筛选。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | ≥1, ≤1000 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1, ≤100 | 每页条数 |
| `keyword` | query | string | 否 | 无 | ≤64 | 模糊匹配 `users.nickname` / `users.phone` |
| `letter_mode` | query | string | 否 | 无 | 枚举 `has\|none` | `has`=自白含英文字母、`none`=自白不含英文字母（NULL 视为不含，一并纳入） |
| `letter_lower` | query | bool | 否 | false | true/false | `true`=自白含小写字母 `[a-z]` |
| `digit` | query | bool | 否 | false | true/false | `true`=自白含数字 `[0-9]` |
| `cn_digit` | query | bool | 否 | false | true/false | `true`=自白含中文数字 `[零一二三四五六七八九]` |

#### 请求示例

```http
GET /api/v1/admin/members/media/intros?letter_mode=has&letter_lower=true&keyword=%E5%BE%90
Authorization: Bearer <token>
```

非法示例（`letter_mode` 取非法值）：

```http
GET /api/v1/admin/members/media/intros?letter_mode=upper
```

返回 `422`，`detail` 提示 `letter_mode` 不在 `^(has|none)$` 内。

#### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | 列表 |
| `items[].id` | int | 是 | — | 列表主键（= user_id） |
| `items[].user_id` | int | 是 | — | 会员 user_id，「查看资料」使用 |
| `items[].member_code` | string | 是 | — | 会员编号 `G`+6 位 |
| `items[].nickname` | string\|null | 是 | `null` | 昵称 |
| `items[].avatar` | string\|null | 是 | `null` | 头像 URL |
| `items[].self_intro` | string\|null | 是 | `null` | 自白内容（个人介绍） |
| `items[].updated_at` | string\|null | 是 | `null` | 自白最近修改时间（无资料行时为 null） |
| `page` / `page_size` / `total` / `has_more` | int/bool | 是 | — | 分页元数据 |

#### 返回示例

```json
{
  "items": [
    {
      "id": 424118, "user_id": 424118, "member_code": "G424118", "nickname": "小可爱",
      "avatar": null, "self_intro": "在211大学当老师，喜欢运动", "updated_at": "2026-07-20T14:42:24"
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
- 页面「个人介绍」Tab 进入时调用；修改筛选下拉后点击「搜索」带新参数重查。
- 数据源 `users u LEFT JOIN user_profile p`，用户无资料行时 `self_intro`/`updated_at` 为 `null`，仍会列出。
- 边界：无权限返回 `403`；未登录 `401`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录/令牌失效 | 跳转登录 |
| 403 | 无 `matchmaker.member.read` | 提示无权限 |
| 422 | `letter_mode` 非枚举值 | 修正参数 |

#### 文档完成自检清单

- [x] 请求参数表，含每个参数业务含义
- [x] 含非法示例
- [x] 返回字段业务含义
- [x] 含空数据示例
- [x] 使用方法与错误表

---

### 2.2 `PUT /api/v1/admin/members/media/intros/{user_id}`

#### 基本信息

- 用途：编辑/清空会员个人介绍（自白内容）。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回更新后的 `MemberIntroItem`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `user_id` | path | int | 是 | ≥1 | 会员 user_id |
| `self_intro` | body | string | 是 | ≤500；**允许空串**（空串=清空自白） | 自白内容 |

#### 请求示例

```json
{ "self_intro": "热爱生活，期待遇见对的你" }
```

清空示例（合法）：

```json
{ "self_intro": "" }
```

非法示例（超长）：

```json
{ "self_intro": "这是一段超过五百字的内容……（501字）" }
```

返回 `422`，`self_intro` 超过 500 字符。

#### 返回参数

同 §2.1 的 `MemberIntroItem`（含最新 `updated_at`）。

#### 返回示例

```json
{ "id": 424118, "user_id": 424118, "member_code": "G424118", "nickname": "小可爱",
  "avatar": null, "self_intro": "热爱生活，期待遇见对的你", "updated_at": "2026-09-11T08:00:00" }
```

#### 使用方法与业务规则

- `users` 不存在返回 `404`；`user_profile` 以 `uk_user_id` 唯一键 upsert（无资料行则新建）。
- 写审计 `member.intro.update`，`after_json = {"self_intro": ...}`。
- 前端「自白内容」input 失焦或回车时调用，成功后 toast「已保存」。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | `user_id` 不存在 | 提示不存在 |
| 422 | `self_intro` 超 500 | 提示超长 |

#### 文档完成自检清单

- [x] 参数表（含「允许空串」说明）
- [x] 含非法示例（超长）
- [x] 返回示例
- [x] 使用方法（含审计）

---

## 3. 媒体审核列表（头像/照片/视频）

### 3.1 `GET /api/v1/admin/members/media`

#### 基本信息

- 用途：分页查询指定类型（头像/照片/视频）的媒体，仅返回未软删记录。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `media_type` | query | string | **是** | 无 | 枚举 `avatar\|photo\|video` | 媒体类型 |
| `page` | query | int | 否 | 1 | ≥1, ≤1000 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1, ≤100 | 每页条数 |
| `review_status` | query | int | 否 | 无 | 0–3 | 审核状态过滤（0审核中/1通过/2未通过/3隐藏） |
| `keyword` | query | string | 否 | 无 | ≤64 | 匹配昵称/手机号/会员编号 |
| `gender` | query | int | 否 | 无 | 1/2 | 1男 2女 |

#### 请求示例

```http
GET /api/v1/admin/members/media?media_type=avatar&review_status=2&gender=2
Authorization: Bearer <token>
```

非法示例（`media_type` 缺失或非法）：

```http
GET /api/v1/admin/members/media?media_type=doc
```

返回 `422`，`media_type` 非枚举值（缺失则 422 必填校验）。

#### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `items` | array | 是 | `[]` | 列表 |
| `items[].id` | int | 是 | — | 媒体记录主键（user_media.id） |
| `items[].user_id` | int | 是 | — | 会员 user_id |
| `items[].member_code` | string | 是 | — | 会员编号 G+6 位 |
| `items[].nickname` | string\|null | 是 | `null` | 昵称 |
| `items[].avatar` | string\|null | 是 | `null` | 会员头像 URL |
| `items[].media_type` | string | 是 | — | avatar/photo/video |
| `items[].file_url` | string\|null | 是 | `null` | 媒体文件地址（前端需经 `resolveMediaUrl` 解析） |
| `items[].thumbnail_url` | string\|null | 是 | `null` | 缩略图地址（视频/部分图片） |
| `items[].mime_type` | string\|null | 是 | `null` | 文件 MIME 类型 |
| `items[].duration_seconds` | int\|null | 是 | `null` | 视频时长（秒） |
| `items[].review_status` | int | 是 | — | 0审核中/1通过/2未通过/3隐藏 |
| `items[].review_status_label` | string | 是 | — | 审核状态中文标签 |
| `items[].review_reason` | string\|null | 是 | `null` | 审核不通过/隐藏原因 |
| `items[].age` | int\|null | 是 | `null` | 由生日推算的年龄（无生日为 null） |
| `items[].meta_text` | string\|null | 是 | `null` | 形如 "1990年 175cm 大专"，缺项跳过；全空为 null |
| `items[].created_at` | string\|null | 是 | `null` | 上传时间 |
| `page` / `page_size` / `total` / `has_more` | int/bool | 是 | — | 分页元数据 |

#### 返回示例（avatar）

```json
{
  "items": [
    {
      "id": 881, "user_id": 396140, "member_code": "G396140", "nickname": "lll", "avatar": null,
      "media_type": "avatar", "file_url": "/uploads/avatar/396140.jpg", "thumbnail_url": null,
      "mime_type": "image/jpeg", "duration_seconds": null, "review_status": 0, "review_status_label": "审核中",
      "review_reason": null, "age": 19, "meta_text": "2007年 170cm 大专", "created_at": "2026-08-21T10:48:23"
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

- `age` 由 `TIMESTAMPDIFF(YEAR, u.birthday, CURDATE())` 推算；`meta_text` = `生日年份 + 身高 + 学历` 经 `CONCAT_WS(' ', ...)` 拼接（NULL 自动跳过，全 NULL 返回 null）。
- 前端头像 Tab 两个状态下拉默认映射：`待审核`→0、`已通过`→1、`未通过`→2（下拉②默认值「未通过」即 `review_status=2`）。
- 文件地址统一经 `resolveMediaUrl` 解析后再渲染。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录 | 跳转登录 |
| 403 | 无 `matchmaker.member.read` | 提示无权限 |
| 422 | `media_type` 缺失/非法、`review_status` 越界 | 修正参数 |

#### 文档完成自检清单

- [x] 请求参数表（含必填 media_type）
- [x] 含非法示例
- [x] 返回字段业务含义（含 age/meta_text 计算说明）
- [x] 含空数据示例
- [x] 使用方法与错误表

---

### 3.2 `GET /api/v1/admin/members/media/{user_id}/history`

#### 基本信息

- 用途：查询会员**全部头像历史**（含已软删记录），用于「历史头像」弹窗。
- 权限：`matchmaker.member.read`。
- 成功状态：`200 OK`，返回 `MemberMediaPage`（结构同 §3.1）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `user_id` | path | int | 是 | 无 | ≥1 | 会员 user_id |
| `page` | query | int | 否 | 1 | ≥1, ≤1000 | 页码 |
| `page_size` | query | int | 否 | 20 | ≥1, ≤100 | 每页条数 |

#### 请求示例

```http
GET /api/v1/admin/members/media/396140/history
Authorization: Bearer <token>
```

#### 返回参数

同 §3.1 的 `MemberMediaPage`（_items 固定 `media_type="avatar"`，含 `deleted_at` 非空的历史记录）。

#### 返回示例

```json
{ "items": [
  { "id": 881, "user_id": 396140, "member_code": "G396140", "nickname": "lll", "avatar": null,
    "media_type": "avatar", "file_url": "/uploads/avatar/old.jpg", "thumbnail_url": null,
    "mime_type": "image/jpeg", "duration_seconds": null, "review_status": 2, "review_status_label": "未通过",
    "review_reason": null, "age": 19, "meta_text": "2007年 170cm 大专", "created_at": "2026-06-01T10:00:00" }
], "page": 1, "page_size": 20, "total": 1, "has_more": false }
```

#### 使用方法与业务规则

- `user_id` 不存在返回 `404`。
- 与 §3.1 不同：本接口**不排除已软删头像**，完整还原头像变更轨迹。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 会员不存在 | 提示不存在 |
| 401/403 | 未登录/无权限 | 跳转或提示 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回示例
- [x] 使用方法（含「含已删」差异）
- [x] 错误表（含 404）

---

## 4. 媒体写操作

### 4.1 `PATCH /api/v1/admin/members/media/{media_id}`

#### 基本信息

- 用途：审核媒体：通过(1)/未通过(2)/隐藏(3)。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "review_status": int }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `media_id` | path | int | 是 | ≥1 | 媒体记录主键 |
| `review_status` | body | int | 是 | 1/2/3 | 1通过 2未通过 3隐藏（不可置 0） |
| `review_reason` | body | string\|null | 否 | ≤255 | 不通过/隐藏原因 |

#### 请求示例

```json
{ "review_status": 2, "review_reason": "含违规内容" }
```

非法示例（`review_status=0` 或 `4`）：

```json
{ "review_status": 0 }
```

返回 `422`，`review_status` 不在 1–3 范围。

#### 返回示例

```json
{ "id": 881, "review_status": 2 }
```

#### 使用方法与业务规则

- 头像 Tab「不通过」按钮调本接口传 `review_status=2`。
- `media_id` 不存在返回 `404`；写审计 `member.media.review`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 媒体记录不存在 | 提示不存在 |
| 422 | `review_status` 非法（0/4） | 提示 |

#### 文档完成自检清单

- [x] 参数表（review_status 枚举）
- [x] 含非法示例
- [x] 返回示例 + 审计说明
- [x] 错误表

---

### 4.2 `PUT /api/v1/admin/members/media/{media_id}`

#### 基本信息

- 用途：媒体重新上传：覆盖文件地址，并将审核状态归零（待审核）。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "file_url": string }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `media_id` | path | int | 是 | ≥1 | 媒体记录主键 |
| `file_url` | body | string | 是 | 1–512 | 新文件地址（先经 `POST /admin/common/upload` 上传） |
| `thumbnail_url` | body | string\|null | 否 | ≤512 | 新缩略图地址 |

#### 请求示例

```json
{ "file_url": "/uploads/avatar/396140_new.jpg", "thumbnail_url": null }
```

非法示例（`file_url` 为空串）：

```json
{ "file_url": "" }
```

返回 `422`，`file_url` 不能为空。

#### 返回示例

```json
{ "id": 881, "file_url": "/uploads/avatar/396140_new.jpg" }
```

#### 使用方法与业务规则

- 头像 Tab「重新上传」按钮：先 `pickAndUploadImage` 拿到 URL，再调本接口；成功后状态归 0（待审核），前端重新拉取列表。
- `media_id` 不存在返回 `404`；写审计 `member.media.replace`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 媒体记录不存在 | 提示不存在 |
| 422 | `file_url` 为空/超长 | 提示 |

#### 文档完成自检清单

- [x] 参数表（含 file_url 必填非空）
- [x] 含非法示例（空串）
- [x] 返回示例 + 业务规则
- [x] 错误表

---

### 4.3 `DELETE /api/v1/admin/members/media/{media_id}`

#### 基本信息

- 用途：软删除媒体（`deleted_at = UTC_TIMESTAMP()`）。
- 权限：`matchmaker.member.manage`。
- 成功状态：`200 OK`，返回 `{ "id": int, "deleted": true }`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- | --- |
| `media_id` | path | int | 是 | 媒体记录主键 |

#### 返回示例

```json
{ "id": 881, "deleted": true }
```

#### 使用方法与业务规则

- 软删后不再出现在 §3.1 列表，但仍可在 §3.2 历史中查到。
- 写审计 `member.media.delete`。

#### 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 404 | 媒体记录不存在 | 提示不存在 |
| 401/403 | 未登录/无权限 | 跳转或提示 |

#### 文档完成自检清单

- [x] 参数表
- [x] 返回示例
- [x] 使用方法（软删差异）
- [x] 错误表

---

## 5. 总览：本模块全部端点

| Method | Path | 权限 |
| --- | --- | --- |
| GET | `/api/v1/admin/members/media/intros` | matchmaker.member.read |
| PUT | `/api/v1/admin/members/media/intros/{user_id}` | matchmaker.member.manage |
| GET | `/api/v1/admin/members/media` | matchmaker.member.read |
| GET | `/api/v1/admin/members/media/{user_id}/history` | matchmaker.member.read |
| PATCH | `/api/v1/admin/members/media/{media_id}` | matchmaker.member.manage |
| PUT | `/api/v1/admin/members/media/{media_id}` | matchmaker.member.manage |
| DELETE | `/api/v1/admin/members/media/{media_id}` | matchmaker.member.manage |

> 路由冲突规避：`member_records_admin.py` 已有 `GET /admin/members/{member_id}/media`（3 段，单会员媒体分页）。本模块使用 `/admin/members/media`（2 段）、`/admin/members/media/intros`（静态 3 段）、`/admin/members/media/{user_id}/history`（4 段，末段静态 `history`）、`/admin/members/media/{media_id}`（动态 2 段）。段数/末段字面均不冲突，且静态路径在文件中先于动态 `/media/{media_id}` 注册，不会被抢占。
