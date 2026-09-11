# 移动端红娘工作台 API

> 版本：2026-09-02。所有接口都挂载在 `/api/v1` 下，使用普通用户 JWT：`Authorization: Bearer <access_token>`。这些接口不是 `/admin/*` 管理后台的替代入口。

## 共同约定

| 项目 | 说明 |
| --- | --- |
| Content-Type | `application/json`；GET 请求无请求体 |
| 成功状态 | 读取为 `200`，创建为 `201`，更新为 `200` |
| 分页 | `page` 从 1 开始，`page_size` 为 1–100；返回 `items/page/page_size/total/has_more` |
| 时间 | ISO 8601 UTC 时间字符串；`birthday` 仅返回日期 |
| 兼容性 | 新接口，不修改已有 `/admin`、`/organizations`、`/promotions` 契约 |
| 幂等 | 读取接口天然幂等；资料审核与团队名称更新以当前状态覆盖；生成邀请码/推广码每次生成一个新代码 |

未登录返回 `401`（`{"detail":"请先登录"}`）；账号无对应角色或工作范围返回 `403`；资源不在数据范围或不存在统一返回 `404`；状态冲突、重复联系方式或重复归属返回 `409`；参数校验失败返回 `422`。

## 服务红娘工作台

### 查询工作台准入

**基本信息**：`GET /matchmaker/management-center/access`；需登录；无需单独红娘后台账号。

**请求参数**：无请求体。非法示例：不带 Bearer Token。

**返回参数**：`can_access`（boolean，是否可进入）、`role_status`（`active|inactive`）、`payment_status`（固定 `not_required`，本期不以支付为门槛）、`scope`（`SELF|ORGANIZATION|null`）、`message`（可展示提示）。

**返回示例**：

```json
{"can_access":true,"role_status":"active","payment_status":"not_required","scope":"SELF","message":"服务红娘工作台已开通"}
```

**使用方法与业务规则**：客户端先调用本接口，再请求 dashboard。准入条件是服务红娘申请审核通过且 `user_role.service_matchmaker` 有效；超级服务红娘必须有平台配置的组织范围。普通红娘只可处理分配给自己的资源，超级红娘只可处理其组织范围资源。

### 查询工作台概览

**基本信息**：`GET /matchmaker/management-center/dashboard`；需已通过准入；无请求体。

**返回参数**：`identity.greeting/name/role_name` 为展示信息；`store_metrics` 固定 8 项，每项含 `key/label/value/action`，可选 `value_prefix`（数值前缀，如人民币符号）和 `badge`（指标角标，如今日新增数）。人数、牵线和约见来自当前授权范围；没有财务流水时金额返回 `"0"`，由 `value_prefix="￥"` 表示人民币，不再返回 `"--"`。

**返回示例**：

```json
{"identity":{"greeting":"你好","name":"林老师","role_name":"服务红娘"},"store_metrics":[{"key":"pending_review","label":"资料待审","value":"2","action":"资料待审","badge":"今日+2"},{"key":"online_commission","label":"线上分成","value":"0","action":"分成明细","value_prefix":"￥"}]}
```

### 红娘展示资料

**基本信息**：`GET|PATCH /matchmaker/workbench/profile`；需已通过准入。

**请求参数**：PATCH Body 为 `{"display_name":"林老师"}`；长度 1–64。禁止提交 `level`、`organization_id` 或用户 ID，超级范围仅能由平台配置。

**返回参数**：`user_id`（平台用户 ID）、`display_name`（红娘展示昵称）、`level`（`NORMAL|SUPER`）、`scope`、`organization_id`。展示昵称独立于用户昵称。

**使用方法与业务规则**：PATCH 为覆盖式、可重复调用；写入审计日志。旧客户端不调用该接口不受影响。

### 客源线索

**基本信息**：

| 接口 | 用途 |
| --- | --- |
| `GET /matchmaker/workbench/leads` | 分页查询当前范围线索 |
| `POST /matchmaker/workbench/leads` | 录入并自动归属给当前红娘/组织 |
| `PATCH /matchmaker/workbench/leads/{lead_id}` | 修改当前范围线索 |
| `POST /matchmaker/workbench/leads/{lead_id}/follow-ups` | 新增跟进 |
| `POST /matchmaker/workbench/leads/{lead_id}/abandon` | 弃海 |
| `POST /matchmaker/workbench/leads/{lead_id}/restore` | 恢复弃海 |

所有接口需已通过准入。GET Query：`page/page_size`，可选 `status=NEW|CONTACTED|INTENDED|CONVERTED|LOST|CLOSED`、`search`（最多 64 字）。

创建 Body：`name`（1–128）、`phone`（最多 32）或 `wechat`（最多 128，二者至少其一）、`source`（1–64）、`intention_level`（1–3，默认 1）、可选 `remark`（最多 2000）。跟进 Body：`method=PHONE|WECHAT|VISIT|OTHER`、`content`（1–2000）、可选 `intention_level/next_follow_at`。弃海/恢复 Body：`reason`（1–500）。PATCH 只接受上述可编辑字段，至少一个字段。

线索返回字段：`id/name/phone/wechat/source/intention_level/status/next_follow_at/created_at/updated_at`；跟进额外返回 `lead_id/method/content`。写入均落 `business_audit_log`。**联系方式唯一性**：同手机号或同微信号（去首尾空白后比较，空串视为未提供）在"有效"线索（状态非 `LOST/CLOSED`）中全局唯一；创建、PATCH 修改联系方式、恢复弃海前均会查重，命中返回 `409`（提示占用线索 ID），数据库层由 `active_phone/active_wechat` 生成列唯一索引兜底；弃海、已关闭线索不占用联系方式，可用同一联系方式重新录入或恢复。已成交、关闭或已弃海线索不能再次弃海。普通红娘可以恢复自己弃海的线索；超级红娘可以恢复组织范围弃海线索；恢复时若联系方式已被其它有效线索占用返回 `409`（`该客源的联系方式已被其他有效线索占用，无法恢复`）。

### 资料待审、服务跟进与只读牵线

**基本信息**：

| 接口 | 用途 |
| --- | --- |
| `GET /matchmaker/workbench/members` | 当前范围会员与公开资料审核状态 |
| `PATCH /matchmaker/workbench/members/{member_id}/public-profile-review` | 审核公开资料 |
| `GET|POST /matchmaker/workbench/members/{member_id}/follow-ups` | 查询/新增服务跟进 |
| `GET /matchmaker/workbench/members/{member_id}/contact` | 受审计读取手机号 |
| `GET /matchmaker/workbench/members/{member_id}/match-candidates` | 只读候选人 |
| `GET /matchmaker/workbench/members/{member_id}/match-history` | 只读认识申请历史 |

列表 Query 均为 `page/page_size`；会员列表可附 `review_status=PENDING|PASSED|REJECTED`、`search`（昵称或 ID，最多 64）。审核 Body：`status=PASSED|REJECTED`、可选 `reason`（最多 500；REJECTED 时必填）。跟进 Body：`method=PHONE|WECHAT|VISIT|OTHER`、`content`（1–2000）、可选 `next_follow_at`。

会员列表项包含 `user_id/nickname/avatar/gender/birthday/hometown/residence/education/job/profile_completion_score/review_status/review_reason/reviewed_at/next_follow_at/created_at`，不含手机号。联系方式接口只返回 `user_id/phone`，每次读取写入审计日志。候选人和历史接口不创建牵线、聊天或联系方式交换，候选人只包含公开展示资料，历史包含 `id/counterpart_user_id/counterpart_nickname/status/created_at/responded_at`。

**业务边界**：公开资料审核不改变实名认证或学历认证的状态；跨分配、跨组织访问统一 404，避免确认其他会员存在。审核、跟进、联系读取均有审计记录。

### 牵线管理

**基本信息**：`GET /matchmaker/workbench/introductions`；需已通过工作台准入。Query 为 `page/page_size`，可选 `status=PENDING|IN_PROGRESS|SUCCEEDED|FAILED` 与 `search`（双方昵称或牵线编号，最多 64 字）。

每条记录返回 `id/status/failure_reason/note/created_at/updated_at/from_user/to_user`；双方资料只包含 `user_id/nickname/avatar/gender`，不含手机、微信和其他联系方式。状态独立于 `match_apply` 的认识申请：`PENDING` 为待我牵线，`IN_PROGRESS` 为牵线中，`SUCCEEDED` 为牵线成功，`FAILED` 为牵线失败。看板的“待我牵线”和“牵线成功”直接按该表的 `PENDING` 与 `SUCCEEDED` 统计，因此与列表筛选结果一致。

### 约见申请

**基本信息**：`GET /matchmaker/workbench/meeting-requests`；需已通过工作台准入。Query 为 `page/page_size`，可选 `search`（双方昵称或申请编号，最多 64 字）。

每条记录返回申请的 `id/status/note/created_at/updated_at`、双方公开资料 `applicant/target`，以及可选的最新 `meeting`（仅含 `id/status/scheduled_at/location/cancel_reason`）。不返回双方联系方式，也不在移动工作台伪造处理或安排约见的写操作。申请状态为 `SUBMITTED/CONTACTED/ACCEPTED/DECLINED/CLOSED`，实际约见状态为 `SCHEDULED/REMINDED/CHECKED_IN/COMPLETED/CANCELLED/NO_SHOW`。

看板的“约见申请”仅统计 `SUBMITTED` 的待处理申请；“待见面”只统计已安排、状态为 `SCHEDULED/REMINDED/CHECKED_IN` 的实际约见。列表会把申请跟进、待见面、已完成和已结束分开呈现，避免把历史或未安排记录计入待见面。

## 合伙人中心

### 查询、建队与邀请码

| 接口 | 请求 | 返回 |
| --- | --- | --- |
| `GET /matchmaker/partner-center` | 无请求体 | `profile/team_members/effective_members/finance_available` |
| `PUT /matchmaker/partner-center/team` | `{"name":"华东团队"}`，2–128 字 | 更新后的中心快照 |
| `POST /matchmaker/partner-center/invites` | 无请求体 | `code/created_at` |

三者都需要有效 `partner` 角色。`profile` 含当前用户昵称、团队名和最近邀请码；`team_members` 只含 `user_id/nickname/joined_at`；`effective_members` 只含 `user_id/nickname/gender/register_at/promoter_name`。`finance_available` 固定为 `false`，本期不返回余额、奖励、分成或提现数据。

PUT 只会创建/更新当前用户自己拥有的团队，不能提交 `owner_user_id`。生成的邀请码只供团队邀请流程使用，不能当作推广归属码；所有写入有审计。没有 `partner` 角色返回 403，团队状态不可用返回 409。

## 推广红娘中心

### 查询中心、录入客源与生成推广码

| 接口 | 请求 | 返回 |
| --- | --- | --- |
| `GET /matchmaker/promoter-center` | 无请求体 | 推广中心快照 |
| `POST /matchmaker/promoter-center/leads` | 客源 Body | 新建客源 |
| `POST /matchmaker/promoter-center/promotion-codes` | 无请求体 | `code/created_at` |

需要有效 `promoter` 角色。中心快照的 `profile` 为 `nickname/promotion_code`；`leads` 使用上述线索字段且只返回由当前推广红娘创建的数据；`effective_members` 与 `incomplete_members` 为当前推广归属下会员的 `user_id/nickname/gender/register_at/review_status`；`finance_available=false`。

客源 Body：`name`（1–128）、`phone` 或 `wechat`（至少一个，去首尾空白，空串视为未提供）、`source`（1–64）、可选 `remark`（最多 2000）。后端从 JWT 推导推广红娘和线索所有者，忽略任何前端提供的 `promoter_id`、`owner_user_id` 或组织 ID。**联系方式唯一性与服务红娘工作台一致**：重复有效联系方式返回 409（含数据库生成列唯一索引兜底）；创建客源与生成推广码都有审计记录。

## 前端处理建议

- `401`：清理登录态并返回登录页；`403`：显示身份/申请状态；`404`：刷新列表，不提示资源归属；`409`：保留用户输入并展示具体原因；`422`：标出相应字段。
- 财务类界面收到 `finance_available=false` 时展示 `--` 与“本期未开放”，不得创建本地金额、奖励、提现或订单记录。
- 不要将手机号、审核结果、工作范围、邀请码或推广码写入本地长期缓存；刷新后始终重新请求服务端。
