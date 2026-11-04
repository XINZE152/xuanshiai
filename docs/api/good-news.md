# 红娘喜讯接口契约（2026-11-04）

> 任务标识：`GN-2026-11-04`  
> 状态：后端 Phase 1 已落地；外部前端未在本仓库，未修改。  
> 前缀：`/api/v1`。除特别说明外，均返回 JSON。

## 1. 数据模型

内容主体仍存放在 `admin_content_item`（`domain='good_news'`），流程数据在以下关系表：

| 表 | 用途 |
|---|---|
| `good_news_meta` | 阶段、审核、公开、来源、展示方式、核验等级、祝福计数 |
| `good_news_consent` | 双方同意记录（`FROM` / `TO`），支持撤回 |
| `good_news_blessing` | 祝福明细，唯一键 `(news_id, user_id)` |
| `good_news_report` | 报喜提交记录与审核状态 |
| `good_news_audit` | 提交、审核、发布、下架、同意、撤回审计 |

迁移：`migrations/business/20261104_01_good_news_*`，已登记 `migrations/business/manifest.json`。

### 1.1 状态机

`review_status`：

- `PENDING` → `APPROVED` / `REJECTED`
- `REJECTED` 可重新提交为新记录；不原地回退

`public_status`：

- `DRAFT`：审核通过但未发布
- `PUBLISHED`：已发布，公开列表可见
- `UNPUBLISHED`：已下架，公开列表不可见

发布条件：`review_status=APPROVED`、内容 `status=1`、`FROM`/`TO` 两条同意记录均存在且 `revoked_at IS NULL`。

行级 `status` 仍表示 `1 正常 / 2 停用`，不是审核状态。

### 1.2 展示方式

| `display_mode` | 公共列表/详情 |
|---|---|
| `FULL` | 返回姓名与头像原值 |
| `MASKED` | 姓名首字 + `*`，头像保留 |
| `ANONYMOUS` | 姓名显示“匿名会员”，头像为空 |

工作台接口不脱敏；公共接口不返回手机号、微信号、精确住址。

## 2. 权限矩阵

| 动作 | 登录 | 实名 | 业务资格 |
|---|---|---|---|
| 浏览公开喜讯 | 是 | 否 | 无 |
| 送/撤祝福 | 是 | 是 | 每条一次；不能给自己送 |
| 会员报喜 | 是 | 是 | 无 |
| 工作台列表/代登记 | 是 | 红娘准入 | `require_workspace` 范围 |
| 后台审核/发布/下架 | 后台权限 | — | `platform.config.write` |
| 后台查看同意记录 | 后台权限 | — | `platform.config.read` |

## 3. 公共/会员端接口

### 3.1 读取栏目配置

`GET /api/v1/good-news/config`

- 需要登录。
- 返回 `tools_good_news` 的公共字段；读取失败时使用内置默认配置。
- 后台「锦旗飘飘」统一返回为「锦旗飘扬」，并带 `is_pennant=true`。

返回字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `title` | string | 栏目标题 |
| `description` | string | 栏目描述 |
| `share_cover_url` | string \| null | 分享封面 |
| `banner_url` | string \| null | 宣传头图 |
| `categories` | object[] | 分类，含 `label/value/icon_url/sort_order/is_pennant` |
| `blessings` | string[] | 后台预设祝福语 |

### 3.2 浏览公开喜讯

`GET /api/v1/good-news?stage=&page=1&page_size=20`

- 需要登录。
- `stage` 仅接受 7 个阶段：牵手成功、恋爱生活、已见父母、已订婚、已领证、已办婚礼、婚后生活。
- 只返回 `review_status=APPROVED AND public_status=PUBLISHED` 且双方同意未撤回的内容。
- 同时校验同意 scope：正文必须有 `content`；非匿名展示必须有 `name`；有生活照必须有 `photos`。
- 按 `published_at DESC, id DESC` 排序。

返回 `GoodNewsPage`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `items[].id` | integer | 喜讯 ID |
| `items[].stage` / `stage_label` | string | 阶段值/展示文案 |
| `items[].from_user_id` / `to_user_id` | integer \| null | 工作台返回会员 ID；公共接口固定返回 `null`，避免枚举资料 |
| `items[].from_name` / `to_name` | string \| null | 按 `display_mode` 脱敏后的展示名 |
| `items[].from_avatar` / `to_avatar` | string \| null | 头像；仅 `FULL` 返回真实头像，`MASKED`/`ANONYMOUS` 固定为空，前端按 `display_mode` 渲染占位 |
| `items[].matchmaker_id` | integer \| null | 服务红娘 ID |
| `items[].matchmaker_name` | string \| null | 服务红娘昵称，缺失回落“服务红娘” |
| `items[].meet_duration` | string \| null | 原始时长文案 |
| `items[].meet_duration_text` | string \| null | 统一格式化，如“8 个月”“1 年 2 个月”；前端自行加“相识”前缀 |
| `items[].images` | string[] | 生活照 URL，最多 9 张 |
| `items[].content` | string | 正文纯文本；前端按文本渲染 |
| `items[].blessing` | string \| null | 祝福语文案 |
| `items[].blessing_count` | integer | 祝福人数 |
| `items[].blessed_by_me` | boolean | 当前用户是否已祝福 |
| `items[].verification_level` | string | `PLATFORM_VERIFIED` / `MATCHMAKER_FOLLOWUP` / `SELF_REPORTED` |
| `items[].display_mode` | string | `FULL` / `MASKED` / `ANONYMOUS` |
| `items[].review_status` / `public_status` | string | 工作流状态 |
| `items[].pending_review` | boolean | 是否待审 |
| `items[].created_at` / `published_at` | string \| null | ISO 8601 |
| `page/page_size/total/has_more` | integer/boolean | 分页字段 |

返回示例：

```json
{
  "items": [{
    "id": 123,
    "stage": "已订婚",
    "stage_label": "已订婚",
    "from_user_id": 31,
    "to_user_id": 42,
    "from_name": "林*",
    "to_name": "王*",
    "from_avatar": "/storage/uploads/31/avatar.webp",
    "to_avatar": "/storage/uploads/42/avatar.webp",
    "matchmaker_id": 12,
    "matchmaker_name": "李老师",
    "meet_duration": "8",
    "meet_duration_text": "8 个月",
    "images": ["/storage/uploads/good_news/12/a.webp"],
    "content": "感谢红娘牵线，我们订婚了。",
    "blessing": "愿你们往后余生都有彼此。",
    "blessing_count": 12,
    "blessed_by_me": false,
    "verification_level": "MATCHMAKER_FOLLOWUP",
    "display_mode": "MASKED",
    "review_status": "APPROVED",
    "public_status": "PUBLISHED",
    "pending_review": false,
    "created_at": "2026-11-01T10:00:00",
    "published_at": "2026-11-02T10:00:00"
  }],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

空数据：

```json
{ "items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false }
```

错误：

| HTTP | 触发条件 | 前端处理 |
|---|---|---|
| 401 | 未登录 | 跳登录 |
| 422 | `stage` 非法、分页越界 | 按字段提示/重置筛选 |

### 3.3 公开喜讯详情

`GET /api/v1/good-news/{news_id}`

- 需要登录。
- 只返回公开条件满足的内容；不满足统一返回 404，避免探测。
- 返回字段同 `GoodNewsItem`。

### 3.4 送祝福

`POST /api/v1/good-news/{news_id}/blessings`

- 需要登录 + 实名。
- 同一用户同一喜讯只计一次；重复请求返回当前计数，不重复加一。
- 不能给自己送祝福。
- 喜讯必须满足公开条件。

返回：

```json
{ "news_id": 123, "blessing_count": 13, "blessed_by_me": true, "blessed": true }
```

> `blessed` 与 `blessed_by_me` 同值，保留兼容旧前端；新调用方优先使用 `blessed_by_me`。

错误：401/403（未实名、自己送）、404（未公开）、422（ID 非法）。

### 3.5 撤销祝福

`DELETE /api/v1/good-news/{news_id}/blessings`

- 需要登录 + 实名。
- 删除明细并把计数减一，最低为 0。
- 返回同 3.4，`blessed_by_me=false`。

### 3.6 会员报喜

`POST /api/v1/good-news/reports`

- 需要登录 + 实名。
- 会员只接受 `partner_name`，必须填写；传 `to_user_id` 返回 422。
- `Idempotency-Key` 可选；同提交人同键重复请求返回首次结果。
- 无幂等键时，同 `couple_key + stage` 已有 `PENDING` 返回 409；`couple_key` 为空时回退为同提交人 + 同阶段 + 同对方称呼 + 24 小时。
- 创建后立即生成待审内容，会员端不会看到未公开内容。

请求体：

| 参数 | 类型 | 必填 | 校验 | 含义 |
|---|---|---|---|---|
| `stage` | string | 是 | 7 阶段枚举 | 当前进展 |
| `partner_name` | string | 会员必填 | ≤64 | 对方称呼；会员报喜必须填写 |
| `to_user_id` | integer | 会员不可传 | ≥1 | 对方会员 ID；仅红娘代登记可用，会员传值返回 422 |
| `meet_duration` | string | 否 | ≤32 | 兼容旧前端的时长字符串 |
| `meet_duration_months` | integer | 否 | 0–1200 | 相识月数，优先于字符串 |
| `content` | string | 是 | 1–2000 | 正文 |
| `images` | string[] | 否 | ≤9；必须是当前提交人通过 `/good-news/photo` 上传的 `/storage/uploads/good_news/{user_id}/` 路径，不能引用他人图片 | 生活照 |
| `blessing` | string | 否 | ≤255 | 祝福语 |
| `display_mode` | string | 否 | `FULL/MASKED/ANONYMOUS`，默认 `MASKED` | 展示方式 |
| `verification_level` | string | 否 | 默认 `SELF_REPORTED` | 核验等级 |
| `source_type` | string | 否 | 默认 `SELF_REPORTED` | 来源类型 |
| `source_id` | integer | 否 | ≥1 | 来源记录 ID |
| `consent_from` | boolean | 否 | 默认 false | 本人同意公开 |
| `consent_to` | boolean | 否 | 默认 false | 对方同意公开 |
| `consent_version` | string | 否 | 默认 `v1` | 同意文案版本 |
| `consent_scopes` | string[] | 否 | 默认姓名/头像/生活照/正文/红娘 | 同意范围 |
| `consent_confirmed` | boolean | 否 | 兼容旧前端 | true 时映射为 `consent_from=consent_to=true`，来源记为会员声明 |

> `verification_level` 由服务端按提交角色设置：会员提交固定为 `SELF_REPORTED`，红娘代登记固定为 `MATCHMAKER_FOLLOWUP`；客户端传值不会提升核验等级，`PLATFORM_VERIFIED` 只能由管理端设置。

请求示例：

```http
POST /api/v1/good-news/reports
Authorization: Bearer <token>
Idempotency-Key: gn-20261104-0001
Content-Type: application/json
```

```json
{
  "stage": "已订婚",
  "partner_name": "王先生",
  "meet_duration_months": 8,
  "content": "感谢红娘牵线，我们订婚了。",
  "images": ["/storage/uploads/good_news/31/a.webp"],
  "display_mode": "MASKED",
  "consent_from": true,
  "consent_to": true,
  "consent_version": "v1"
}
```

返回 `GoodNewsReportItem`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | integer | 报喜记录 ID |
| `news_id` | integer \| null | 生成的内容项 ID |
| `stage` | string | 阶段 |
| `partner_name` | string \| null | 对方称呼 |
| `meet_duration_months` | integer \| null | 相识月数 |
| `content` | string | 正文 |
| `images` | string[] | 图片 |
| `status` | string | `PENDING/APPROVED/REJECTED/WITHDRAWN` |
| `reject_reason` | string \| null | 驳回原因 |
| `submitter_role` | string | `MEMBER` |
| `created_at` / `reviewed_at` | string \| null | 时间 |

错误：

| HTTP | 触发条件 | 前端处理 |
|---|---|---|
| 401/403 | 未登录/未实名 | 跳登录/实名 |
| 409 | 同一对会员该阶段已有待审、24 小时内重复提交或幂等键冲突 | 提示等待审核 |
| 422 | 会员传 `to_user_id`、未填对方称呼、阶段非法、正文为空、图片超限/不属于本人 | 按字段提示 |
| 404 | 红娘代登记关联的会员不存在 | 重新选择 |

### 3.7 我的报喜

`GET /api/v1/good-news/reports/mine?page=1&page_size=20`

- 需要登录；只返回本人记录。
- 按 `created_at DESC` 排序。
- 返回 `GoodNewsReportPage`，字段同 3.6。

`GET /api/v1/good-news/reports/{report_id}`

- 需要登录；只能查本人记录，越权返回 404。

### 3.8 上传喜讯图片

`POST /api/v1/good-news/photo`

- 需要登录 + 实名。
- `multipart/form-data`，字段名 `file`。
- 支持 JPG/JPEG/PNG/WEBP；单张 ≤5MB；像素 ≤2500 万；每条喜讯最多使用 9 张。
- 服务端剥离 EXIF、转 WEBP、质量 85。
- 返回：

```json
{ "url": "/storage/uploads/good_news/31/a.webp", "content_type": "image/webp", "size": 12345 }
```

错误：413（过大/像素过高）、415（格式不支持）、422（空文件）。

### 3.9 撤回展示同意

`POST /api/v1/good-news/{news_id}/consent/revoke?role=FROM|TO`

- 需要登录；`role=FROM` 只能由 `from_user_id` 撤回，`role=TO` 只能由 `to_user_id` 撤回。
- 撤回后立即把 `public_status` 置为 `UNPUBLISHED`，公开流不再展示。
- 写 `good_news_audit`；成功返回 204。
- 红娘代登记且未关联会员（meta 的 from/to 为空）时，会员无法撤回；由后台下架。

## 4. 红娘工作台接口

### 4.1 查询范围喜讯

`GET /api/v1/matchmaker/workbench/good-news?stage=&page=1&page_size=20`

- 需要登录 + 红娘工作台准入（`require_workspace`）。
- 普通红娘只看 `matchmaker_id=自己`；超级红娘看 `organization_id=本组织`。
- 返回：已发布内容 + 本人提交的待审内容；不返回范围外内容。
- 返回 `GoodNewsPage`；字段同 3.2，但不脱敏。

### 4.2 红娘代会员登记

`POST /api/v1/matchmaker/workbench/good-news`

- 需要登录 + 工作台准入。
- 请求体同 3.6；`from_user_id` 可选，`to_user_id` 可选。
- `submitted_by_role=MATCHMAKER`；默认 `verification_level=MATCHMAKER_FOLLOWUP`；默认 `source_type=MANUAL`。
- `consent_from/consent_to` 表示红娘已取得双方同意；同意来源记为 `MATCHMAKER_ATTESTED`。
- 创建后 `review_status=PENDING`、`public_status=DRAFT`。
- 返回 `GoodNewsItem`，`pending_review=true`。
- `Idempotency-Key` 可选；无键时同提交人 + 阶段 + 对方称呼 + 24 小时内重复返回 409。

### 4.3 上传喜讯图片

`POST /api/v1/matchmaker/workbench/good-news/photo`

- 需要登录 + 工作台准入。
- `multipart/form-data`，字段名 `file`。
- 支持 JPG/JPEG/PNG/WEBP；单张 ≤5MB；像素 ≤2500 万。
- 服务端剥离 EXIF、转 WEBP、质量 85。
- 返回：

```json
{ "url": "/storage/uploads/good_news/12/a.webp", "content_type": "image/webp", "size": 12345 }
```

错误：413（过大/像素过高）、415（格式不支持）、422（空文件）。

### 4.4 锦旗墙

`GET /api/v1/matchmaker/workbench/good-news/pennants?page=1&page_size=20`

- 需要登录 + 工作台准入。
- 普通红娘只返回 `extra_json.target=当前红娘昵称` 的锦旗；超级红娘返回组织可见范围（当前实现为全部已启用锦旗）。
- 返回 `items[]`：`id/maker/giver/target/words/image_url/created_at`，以及分页字段。

## 5. 管理端接口

### 5.1 审核列表

`GET /api/v1/admin/content/good_news?page=&page_size=&keyword=&status=&stage=&review_status=&public_status=&matchmaker_id=`

- 需要 `platform.config.read`。
- 返回 `ContentItemPage`；`items[].extra` 额外包含：
  - `stage/review_status/public_status/display_mode/verification_level`
  - `blessing_count/matchmaker_id/from_user_id/to_user_id`
  - `reject_reason/published_at`
  - `consent_from/consent_to/consent_complete`
- 行级 `status` 仍为 `1 正常 / 2 停用`；审核状态只看 `extra.review_status`。
- 通过通用 `POST/PATCH /admin/content/good_news` 创建或编辑内容时，新建 meta 强制为 `PENDING/DRAFT`；`extra.review_status/public_status` 不能绕过审核/发布接口。

### 5.2 审核详情

`GET /api/v1/admin/content/good_news/{news_id}`

- 需要 `platform.config.read`。
- 返回 `ContentItem`，`extra` 字段同上。
- 若内容存在但没有 `good_news_meta`，读取时会补一条默认 `PENDING/DRAFT` 元数据（不自动公开）。

### 5.3 审核

`POST /api/v1/admin/content/good_news/{news_id}/review`

- 需要 `platform.config.write`。
- 请求体：

```json
{ "action": "APPROVE", "reason": null }
```

| `action` | 合法前置状态 | 结果 |
|---|---|---|
| `APPROVE` | `PENDING` | `review_status=APPROVED`，`public_status=DRAFT` |
| `REJECT` | `PENDING` | `review_status=REJECTED`，`public_status=DRAFT`；`reason` 必填 |
| `OFFLINE` | `PUBLISHED` | `public_status=UNPUBLISHED`；`reason` 必填 |

- 写 `good_news_audit`；APPROVE/REJECT 同步更新 `good_news_report.status`。
- 通知提交人审核结果（失败不阻断审核）。
- 返回更新后的 `GoodNewsItem`。

错误：404（不存在）、409（状态不允许）、422（缺少原因）。

### 5.4 发布

`POST /api/v1/admin/content/good_news/{news_id}/publish`

- 需要 `platform.config.write`。
- 前置：`review_status=APPROVED`、内容 `status=1`、`FROM`/`TO` 同意完整且未撤回。
- 同意范围校验：`ANONYMOUS` 至少需要 `content`（有图时加 `photos`）；`MASKED` 再加 `name`；`FULL` 再加 `avatar`。缺少任一范围返回 409。
- 结果：`public_status=PUBLISHED`，`published_at` 首次发布时写入。
- 写审计；返回 `GoodNewsItem`。
- 错误：409（未审核/停用/同意不完整）。

### 5.5 下架

`POST /api/v1/admin/content/good_news/{news_id}/unpublish?reason=...`

- 需要 `platform.config.write`。
- 前置：`public_status=PUBLISHED`。
- 结果：`public_status=UNPUBLISHED`；写审计；返回 `GoodNewsItem`。
- 错误：409（未发布）。

### 5.6 同意记录

`GET /api/v1/admin/content/good_news/{news_id}/consents`

- 需要 `platform.config.read`。
- 返回 `items[]`：`id/news_id/user_id/role/scopes/consent_version/consent_source/consented_at/revoked_at`。

`POST /api/v1/admin/content/good_news/{news_id}/consents`

- 需要 `platform.config.write`。
- 用于补录线下取得的同意：

```json
{
  "role": "FROM",
  "user_id": 31,
  "scopes": ["name", "avatar", "photos", "content", "matchmaker"],
  "consent_version": "v1",
  "source": "ADMIN"
}
```

- 同角色覆盖已有记录，并把 `revoked_at` 置空；写审计；返回最新同意列表。

## 6. 幂等、限流与边界

- 报喜：`Idempotency-Key` 可选；无键时 24 小时业务去重。
- 祝福：`(news_id, user_id)` 唯一约束；重复请求返回当前状态。
- 审核/发布/下架：按状态机拒绝非法流转；审核动作写审计。
- 图片：最多 9 张；单张 5MB；只存 WEBP。
- 公共列表：未公开/待审/未同意统一不可见；详情 404，避免探测。
- 撤回同意：立即下架；不删除历史审计。
- 当前未实现分布式限流；高频祝福/报喜建议在网关或 Redis 层补限流（Phase 2）。

## 7. 前端对接说明

当前外部前端 `E:/前端/xuanshiai/xuanshiai-vue` 不在本仓库，本次未修改。现有 `api/matchmaker.uts` 的工作台映射是扁平字段，与本文 DTO 兼容；需要新增：

- `getGoodNewsList` / `getGoodNewsDetail` / `getGoodNewsConfig`
- `blessGoodNews` / `unblessGoodNews`
- `createGoodNewsReport` / `getMyGoodNewsReports` / `getMyGoodNewsReport`
- `createWorkspaceGoodNews`（已有）补齐 `consent_from/consent_to` 和 `Idempotency-Key`
- `GET /matchmaker/workbench/good-news/pennants`
- 公共页路由与入口；`pages.json` 受保护，需单独授权。

前端上线前需在 `api/matchmaker-remote.uts` 的 `REMOTE_OPERATIONS` 中加入已部署的 good-news 操作，未部署前保持“远端暂未开放”。

## 8. P1 加固规则（2026-11-04）

| 规则 | 行为 |
|---|---|
| 公共 scope 持续校验 | 列表/详情同时检查 `content`、`name`（非匿名）、`photos`（有图）；缺 scope 不返回 |
| 发布后内容变更 | 管理端修改正文/图片/标题/展示方式/阶段/时长/同意范围时，已发布内容转 `UNPUBLISHED`，否则转 `DRAFT`；`review_status=PENDING`，清空发布时间，写 `CONTENT_CHANGED` 审计 |
| 同意记录变更 | 已发布内容补录/修改同意后立即 `UNPUBLISHED`，需管理员重新发布 |
| 图片归属 | 会员/红娘报喜只能引用自己 `/storage/uploads/good_news/{user_id}/` 下的图片 |
| 撤回角色 | FROM 只能由发起方撤，TO 只能由对方撤；未关联会员的登记不允许会员撤回 |
| MASKED 头像 | 仅 `FULL` 返回真实头像并校验 `avatar` scope；`MASKED`/`ANONYMOUS` 返回空头像，前端渲染占位；服务红娘姓名按平台服务信息展示，不强制会员 `matchmaker` scope |
| 会员关联对象 | 会员报喜只接受 `partner_name`，传 `to_user_id` 返回 422；关系关联由红娘代登记或后续服务端从业务记录推导 |
| 一 Couple 一故事 | 本期不加 `couple_key` 唯一约束；同 `couple_key + stage + PENDING` 只允许一条待审；如产品改为“一 Couple 一故事”，另建 story/history 表。会员侧无 `to_user_id` 时 `couple_key` 只能基于提交人+称呼，跨双方账号无法完全去重，需红娘/管理员关联后合并 |
