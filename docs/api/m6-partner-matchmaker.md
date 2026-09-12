# 合伙红娘 接口契约（M6）

> **模块范围**：管理后台「合伙红娘」菜单下 5 个页面
> - 功能配置 `/love-partner-config`
> - 分成配置 `/love-partner-bonus-config`
> - 合伙人管理 `/love-partner-list`
> - 团队关系 `/love-partner-relation`
> - 分成明细 `/love-partner-bonus-details`
>
> **后端文件**：
> 1. 合伙人管理 / 团队关系 / 分成明细：`app/api/routes/partner_admin.py`
> 2. 分成配置（3 固定级别）：`app/api/routes/partner_level_admin.py`
>
> 所有端点均按 `PROJECT_RULES.md` 2.1.1 的统一模板撰写。本文档随代码演进同步维护。

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：**红娘后台独立 Token**（非 C 端 token）
- 请求头：`Authorization: Bearer <access-token>`
- 鉴权函数：`get_current_matchmaker_admin`
- 所有 admin 端点未登录 → **401**；权限点缺失 → **403**

### 1.2 权限点

| 路径前缀 | 读取权限 | 写入权限 |
|---|---|---|
| `/admin/partners/*` | `matchmaker.read` | `matchmaker.manage` |
| `/admin/partner-relations/*` | `matchmaker.read` | `matchmaker.manage` |
| `/admin/partner-levels/*` | `matchmaker.read` | `matchmaker.manage` |
| 配置域 `tools_love_partner`（功能配置页） | 走平台配置通用接口 `/admin/configs/...` | 同左 |

依赖文件：`app/api/dependencies.py:_matchmaker_admin_permission(request)` 按路径子串映射。
`/partner-levels` 与 `/partner-relations` 均**不含** `/partners` 子串，三条映射互不干扰。

### 1.3 响应与异常

- 列表响应统一为 `{ items, page, page_size, total, has_more }`。
- **金额字段一律以字符串序列化**（`"12.34"`），避免 JS Number 精度丢失；schema 层类型为 `Decimal`（pydantic JSON 模式自动转字符串）。
- 日期筛选参数格式 `^\d{4}-\d{2}-\d{2}$`；`end_date` 为**闭区间**（服务端按 `< DATE_ADD(end_date, INTERVAL 1 DAY)` 处理）。

### 1.4 数据库变更

| 对象 | 变更 | 说明 |
|---|---|---|
| `partner_level_config` | **新建表** | 合伙红娘分成级别配置，固定 3 行（`uk_partner_level(level_id)`），不可新增/删除/重排 |
| `partner_team` | 补列 `level_id` | `tinyint unsigned NOT NULL DEFAULT 1`，合伙级别 1 初级 / 2 中级 / 3 战略合伙人 |
| `commission_entry` | 补列 `source` / `remark`；`order_id` 放开为可空 | 支持后台「录入一笔分成」；`source='manual'` |
| `account_ledger` | 无变更 | 手工录入分成会同时写一条 `CREDIT/AVAILABLE` 账本，计入合伙人余额 |

迁移入口：`database_setup_marriage.py` → `DatabaseManager._ensure_m6_columns(cursor)`（已在 `_ensure_*` 主流程注册，幂等）。

**种子数据**（`INSERT IGNORE`）：

| level_id | level_name | auto_split_mode | auto_split_rate | 业绩阈值 | 会员阈值 |
|---|---|---|---|---|---|
| 1 | 初级合伙人 | auto_rate | 35% | — | — |
| 2 | 中级合伙人 | auto_rate | 40% | 10000 元 | 100 人 |
| 3 | 战略合伙人 | auto_rate | 45% | 30000 元 | 500 人 |

---

## 二、合伙人管理（`/api/v1/admin/partners`）

> **口径**：合伙人 = `partner_team` 一行（`owner_user_id` 唯一）。团队名即合伙人团队名。

### 2.1 列表 `GET /admin/partners`

**query 参数**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | 1-1000 | 页码 |
| `page_size` | int | 20 | 1-100 | 每页条数 |
| `keyword` | string \| null | — | ≤100 | 关键字（账号昵称 / 团队名 / 手机） |
| `level_id` | int \| null | — | 1-3 | 合伙级别 |
| `status` | int \| null | — | 1-3 | 1 正常 2 关闭 3 冻结 |
| `sort` | string | `created_desc` | 枚举 | `created_desc` / `created_asc` / `performance_desc` / `member_desc` |

**返回** `PartnerStaffPage`，item 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` / `team_id` | int | 团队 ID |
| `user_id` | int | 合伙人 owner user id |
| `account` | string \| null | 账号昵称 |
| `display_name` | string \| null | 展示名（回退账号昵称） |
| `avatar` | string \| null | 头像 |
| `phone` | string \| null | 手机号 |
| `team_name` | string | 团队名称 |
| `level_id` / `level_name` | int / string | 合伙级别 |
| `member_count` | int | **团队成员**：`partner_membership(status=1)` 计数 |
| `performance_amount` | string | **团队业绩**：团队成员名下会员的已支付订单总额 |
| `effective_member_count` | int | **团队有效会员**：资料审核通过（`matchmaker_member_review.status='PASSED'`）的会员数 |
| `commission_amount` | string | **累积分成**：`commission_entry(beneficiary_type='partner', beneficiary_id=owner_user_id)` 且 `status<>'REVERSED'` 之和 |
| `status` / `status_label` | int / string | 团队状态 |
| `open_mode` | string | `manual` 人工开通 / `paid` 在线付费开通 |
| `created_at` | datetime | 创建时间 |

**空数据示例**：`{"items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false}`

### 2.2 添加 `POST /admin/partners`

**body** `PartnerStaffCreate`：

| 字段 | 必填 | 校验 | 含义 |
|---|---|---|---|
| `user_id` | 二选一 | ≥1 | 直接指定用户 |
| `lookup` | 二选一 | ≤64 | 按昵称/手机搜索绑定 |
| `lookup_by` | 否 | `nickname` / `phone` | 默认 `nickname` |
| `team_name` | 是 | 1-128 | 团队名称 |
| `level_id` | 否 | 1-3 | 默认 1 |
| `open_mode` | 否 | `manual` / `paid` | 默认 `manual` |

**返回**：201 `PartnerStaffDetail`。

**业务规则**：
- `user_id` 与 `lookup` 至少提供一个，否则 422。
- 用户不存在或已停用 → 404。
- **服务红娘不能成为合伙人**（存在 `user_matchmaker_apply(application_type='service_matchmaker')`）→ 409。
- 该用户已是合伙人（`partner_team` 已存在）→ 409。
- 成功后写 `user_role(user_id, 'partner', 1)`。
- 若该用户本身已是推广红娘且当前无团队 → 自动加入自己的团队（与客户端 `get_partner_center` 口径一致）。

**非法示例**：`POST` body `{"team_name": "校园红娘小队"}`（既无 `user_id` 也无 `lookup`）→ 422。

### 2.3 统计卡 `GET /admin/partners/statistics`

无参数。返回 `PartnerStatistics`：

| 字段 | 含义 |
|---|---|
| `total_partners` | 合伙人总数（团队行数） |
| `active_partners` | 状态正常（status=1）的合伙人数 |
| `total_members` | 全部团队成员数 |
| `total_effective_members` | 全部团队有效会员数 |
| `total_performance` | 全部团队业绩合计（字符串） |
| `total_commission` | 全部分成合计（字符串） |

### 2.4 候选用户 `GET /admin/partners/user-candidates`

**query**：`keyword`（必填，1-64）、`limit`（默认 10，1-50）。

**返回** `list[PartnerUserCandidate]`：`id / nickname / real_name / phone / avatar / is_promoter / has_team`。

**业务规则**：排除服务红娘；`is_promoter` 表示已是推广红娘，`has_team` 表示已是合伙人（两者仅作前端提示，不阻断）。

### 2.5 团队下拉 `GET /admin/partners/team-options`

无参数。返回 `list[PartnerTeamOption]`：`id / name / owner_user_id / owner_name / level_id / level_name`。仅取 `status=1` 的团队，按 id 倒序。

### 2.6 详情 `GET /admin/partners/{team_id}`

**path**：`team_id`（≥1）。

**返回** `PartnerStaffDetail` = 列表 item + `owner_nickname` / `owner_phone` / `invite_code`（团队邀请码，取 `promotion_touch` 最新一条，可能为 `null`）。

不存在 → 404。

### 2.7 编辑 `PUT /admin/partners/{team_id}`

**body** `PartnerStaffUpdate`（全可选，但至少一个字段，否则 422）：`team_name` / `level_id` / `status` / `open_mode`。

**返回** `PartnerStaffDetail`。写审计 `partner.update`。

### 2.8 删除（关闭合伙人）`DELETE /admin/partners/{team_id}`

无 body。

**返回**：`{ team_id, status: 2, removed_members }`。

**业务规则**（软删除，不物理删除）：
1. 该团队所有 `partner_membership(status=1)` → `status=2`，写 `left_at`、`change_reason='合伙人关闭，团队成员移出'`。
2. `partner_team.status` → 2。
3. `user_role(role_code='partner')` → `status=3`，写 `revoked_at` / `revoke_reason`。
4. 写审计 `partner.delete`。

---

## 三、团队关系（`/api/v1/admin/partner-relations`）

### 3.1 列表 `GET /admin/partner-relations`

**query 参数**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` | int | 1 | 1-1000 | 页码 |
| `page_size` | int | 20 | 1-100 | 每页条数 |
| `team_id` | int \| null | — | ≥1 | 按隶属合伙人（团队）筛选 |
| `keyword` | string \| null | — | ≤64 | 推广红娘昵称 / 手机 |
| `status` | int \| null | — | 1-3 | 1 正常 2 移出 3 变更。**不传时默认只看 status=1** |

**返回** `PartnerRelationPage`，item 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int | `partner_membership.id` |
| `promoter_id` / `promoter_name` / `promoter_avatar` / `promoter_phone` | — | 推广红娘 |
| `team_id` / `team_name` | — | 隶属团队 |
| `joined_at` / `left_at` | datetime \| null | 加入 / 离开时间 |
| `member_count` | int | 该推广红娘发展的会员数（`promotion_attribution(status=1)`） |
| `performance_amount` | string | 团队业绩贡献：其名下会员的已支付订单总额 |
| `status` / `status_label` | int / string | 团队关系状态 |
| `change_reason` | string \| null | 变更/移出原因 |

### 3.2 人工绑定 `POST /admin/partner-relations`

**body** `PartnerRelationBind`：

| 字段 | 必填 | 校验 | 含义 |
|---|---|---|---|
| `promoter_user_id` | 二选一 | ≥1 | 直接指定推广红娘 |
| `promoter_lookup` | 二选一 | ≤64 | 按昵称搜索（仅匹配已是推广红娘的用户） |
| `team_id` | 是 | ≥1 | 目标团队 |
| `reason` | 否 | ≤255 | 变更原因 |

**返回**：201 `PartnerRelationResult` `{ promoter_id, team_id, team_name, status: "BOUND", message }`。

**业务规则**：
- 目标团队不存在或 `status<>1` → 400。
- 找不到该推广红娘 → 404。
- 若该推广红娘已隶属同一团队 → 409。
- 已在其它团队时：先把旧关系置为 `status=3`（**变更**）并写 `left_at`，再写新关系（`ON DUPLICATE KEY UPDATE` 兜底 `uk_partner_membership_active`）。
- 写审计 `partner_relation.bind`，`after_json` 含 `previous_team_id`。

### 3.3 移出团队 `POST /admin/partner-relations/{relation_id}/remove`

**body** `PartnerRelationRemove`：`reason`（必填，1-255）。

**返回** `PartnerRelationResult` `{ ..., status: "REMOVED" }`。

**业务规则**：关系不存在 → 404；关系已非生效状态（status≠1）→ 409。置 `status=2` + `left_at`，写审计 `partner_relation.remove`。

---

## 四、分成明细（`/api/v1/admin/partners/commission-entries`）

> **口径**：`commission_entry.beneficiary_type='partner'`，`beneficiary_id` = 团队 `owner_user_id`（与总店红娘的 `'service_matchmaker'`、推广红娘的 `'promoter'`、分店的 `'store'` 靠 `beneficiary_type` 区分）。

### 4.1 列表 `GET /admin/partners/commission-entries`

**query 参数**：

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` / `page_size` | int | 1 / 20 | — | 分页 |
| `partner_id` | int \| null | — | ≥1 | 合伙人（团队 owner）user id |
| `rule_id` | int \| null | — | ≥1 | 分成事件（`commission_rule.id`） |
| `start_date` / `end_date` | string \| null | — | `^\d{4}-\d{2}-\d{2}$` | 时间范围（左闭右开） |

**返回** `PartnerCommissionEntryPage`，item 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` / `created_at` | — | 流水 ID / 时间 |
| `partner_id` / `partner_name` | — | 合伙人（团队 owner） |
| `team_id` / `team_name` | — | 所属团队 |
| `promoter_id` / `promoter_name` | — | **团队推广红娘**：由购买账号的 `promotion_attribution` 反查（手工录入时为空） |
| `event_type` | string \| null | **分成类型**：`commission_rule.name`（回退 `payment_order.product_name`） |
| `event_name` | string \| null | **分成事件**：`相亲会员{nickname}(Q{user_id}) - {性别} - {事件名}` |
| `consumer_id` / `consumer_name` | — | 购买会员 |
| `order_id` / `order_no` | int \| null | 关联订单（手工录入为空） |
| `base_amount` / `amount` | string | 计费基数 / 分成金额 |
| `status` | string | `PENDING` / `AVAILABLE` / `FROZEN` / `REVERSED` |
| `source` | string | `order` 订单产生 / `manual` 后台手工录入 |
| `remark` | string \| null | 手工录入备注 |

### 4.2 筛选项 `GET /admin/partners/commission-entries/options`

无参数。返回 `PartnerCommissionEntryOptions`：

- `partners[]`：`{ id, name, team_id, team_name, avatar }`，来源为 `status=1` 的团队
- `events[]`：`{ id, name }`，来源为 `commission_rule(beneficiary_type='partner', status=1)`

### 4.3 录入一笔分成 `POST /admin/partners/commission-entries`

**body** `PartnerCommissionEntryCreate`：

| 字段 | 必填 | 校验 | 含义 |
|---|---|---|---|
| `partner_user_id` | 是 | ≥1 | 合伙人（团队 owner）user id |
| `consumer_user_id` | 否 | ≥1 | 购买账号 |
| `rule_id` | 否 | ≥1 | 分成事件（须为 `beneficiary_type='partner'` 且 `status=1`） |
| `amount` | 是 | >0，≤1000000，2 位小数 | 分成金额(元) |
| `base_amount` | 否 | ≥0 | 计费基数，缺省等同 `amount` |
| `remark` | 否 | ≤255 | 备注 |

**返回**：201 `PartnerCommissionEntryCreateResult` `{ entry, ledger_id, balance_after }`。

**业务规则**：
1. 合伙人团队必须存在且 `status=1`，否则 400。
2. `rule_id` 必须命中启用中的 `partner` 分成规则，否则 400。
3. 写 `commission_entry`：`order_id=NULL`、`status='AVAILABLE'`、`source='manual'`、`idempotency_key='partner-manual-{uuid4}'`。
4. 同事务写 `account_ledger`：`account_type='user'`、`account_id=partner_user_id`、`direction='CREDIT'`、`state='AVAILABLE'`、`source_type='commission_entry'`、`source_id=entry_id`。
5. 写审计 `partner_commission.create`。
6. `balance_after` = 该用户所有非 `REVERSED` 账本的 CREDIT-DEBIT 合计。

> **偏差说明**：页面抽屉内的「短信验证码」为设计稿字段，后台人工录入流程**不校验**该验证码（后端无短信依赖）。「消费事件」为 UI 附属字段，落库时写入 `remark`。

**非法示例**：`{"partner_user_id": 2, "amount": 0}` → 422（`amount` 必须 > 0）。

---

## 五、合伙分成配置（`/api/v1/admin/partner-levels`）

> 级别固定 3 种（1 初级 / 2 中级 / 3 战略合伙人），**不开放新增/删除/重排**；只允许编辑业务参数。

### 5.1 列表 `GET /admin/partner-levels`

无参数。返回 `PartnerLevelPage`，item 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` / `level_id` | int | 行 ID / 级别 ID（1-3） |
| `level_name` | string | 级别名称 |
| `auto_split_mode` | `fixed_amount` / `auto_rate` | 分成模式 |
| `auto_split_mode_label` | string | 列表「分成模式」列展示文案，如 `按比例自动计算：35%` |
| `auto_split_rate` | decimal \| null | 按比例自动计算的比例(%) |
| `promote_performance_threshold` | decimal \| null | 自动升级条件：团队累计业绩阈值(元) |
| `promote_member_threshold` | int \| null | 自动升级条件：团队累计有效会员数阈值 |
| `promote_condition_text` | string | 自动升级条件展示文案，如 `团队累计业绩>=10000元 或 团队累计发展有效相亲会员数量>=100人`；均为空时为 `默认` |
| `partner_count` | int | 该级别合伙人数（`partner_team(level_id, status=1)` 聚合） |
| `register_reward_male` / `register_reward_female` | decimal | 男/女会员注册奖励(元/人) |
| `promoter_join_reward` | decimal | 推广红娘纳入分成(元/人) |
| `consume_commission_mode` | `none` / `auto_rate` | 会员消费分成模式 |
| `consume_commission_rate` | decimal \| null | 会员消费分成比例(%) |
| `share_bonus` | bool | 合伙人同时是自己团队推广红娘时是否享有奖励/分成 |
| `bonus_items` | `[{name, amount}]` | 按事件的分成金额明细（「分成金额」网格） |
| `updated_at` | datetime \| null | — |

### 5.2 详情 `GET /admin/partner-levels/{level_id}`

**path**：`level_id` ∈ [1,3]（超出 → 422）。不存在 → 404。

### 5.3 编辑 `PUT /admin/partner-levels/{level_id}`

**body** `PartnerLevelUpdate`（全可选）：`level_name` / `auto_split_mode` / `auto_split_rate` / `promote_performance_threshold` / `promote_member_threshold` / `register_reward_male` / `register_reward_female` / `promoter_join_reward` / `consume_commission_mode` / `consume_commission_rate` / `share_bonus` / `bonus_items`。

**返回** `PartnerLevelItem`。

**业务规则**：
- `consume_commission_mode='auto_rate'` 且（新值 + 既有值）都无比例 → 400。
- `auto_split_mode='auto_rate'` 且（新值 + 既有值）都无比例 → 400。
- `consume_commission_mode='none'` → 自动把 `consume_commission_rate` 置 NULL。
- 阈值传 `0` 等同「不限制」，按 NULL 存储。
- `bonus_items` 序列化为 json 字符串落库；`share_bonus` 转 tinyint。
- 写审计 `partner_level.update`。

---

## 六、功能配置（配置域 `tools_love_partner`）

页面 `/love-partner-config` 走平台配置通用接口（`/admin/configs/tools_love_partner` 读 + 版本化写），**本模块未新增端点**，仅扩展该配置域的默认字段：

| 字段 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `content_html` | string | `""` | 合伙人介绍文案（富文本 HTML） |
| `enabled` | bool | `true` | 是否开启合伙红娘功能 |
| `apply_tip` | string | `""` | 申请提示文案 |
| `share_bonus` | bool | `true` | **新增**：合伙人同时是自己团队推广红娘时，是否享有该推广红娘的注册会员奖励与消费分成（页面单选「享有/不享有」） |

---

## 七、错误码表

| HTTP | 触发条件 | 前端处理 | JSON 示例 |
|---|---|---|---|
| 401 | 未登录 / token 失效 | 跳登录 | `{"detail": "请先登录红娘后台"}` |
| 403 | 权限点缺失 | 提示无权限 | `{"detail": "????????"}` |
| 404 | 合伙人 / 团队关系 / 级别不存在；用户或推广红娘找不到 | 页面提示并刷新列表 | `{"detail": "合伙人不存在"}` |
| 400 | 团队已关闭；分成事件不存在；比例缺失；购买账号不存在 | 表单内提示 | `{"detail": "合伙团队不存在或已关闭"}` |
| 409 | 已是合伙人；服务红娘不可成为合伙人；已隶属同一团队；关系已非生效 | 表单内提示 | `{"detail": "服务红娘不能成为合伙人"}` |
| 410 | —（本模块未使用） | — | — |
| 422 | 参数校验失败（缺 `lookup`、`amount<=0`、`level_id>3` 等） | 表单内提示 | `{"detail": [{"loc": ["body", "amount"], "msg": "Input should be greater than 0"}]}` |

---

## 八、前端对应页面

| 页面 | 路由 | 主要调用 |
|---|---|---|
| 功能配置 | `/love-partner-config` | 配置域 `tools_love_partner`（读 `content_html` / `share_bonus`，写回） |
| 分成配置 | `/love-partner-bonus-config` | `GET /admin/partner-levels`、`PUT /admin/partner-levels/{id}` |
| 合伙人管理 | `/love-partner-list` | `GET/POST /admin/partners`、`PUT/DELETE /admin/partners/{id}`、`GET /admin/partners/user-candidates` |
| 团队关系 | `/love-partner-relation` | `GET /admin/partner-relations`、`POST /admin/partner-relations`、`POST /admin/partner-relations/{id}/remove`、`GET /admin/partners/team-options` |
| 分成明细 | `/love-partner-bonus-details` | `GET/POST /admin/partners/commission-entries`、`GET /admin/partners/commission-entries/options`、`GET /admin/partners/user-candidates` |

前端 API 层：`src/lib/admin-endpoints.ts`（`partner*` 系列方法 + `Partner*` 类型）。
面包屑分组：`src/lib/breadcrumb-config.ts` 的 `partner`。

---

## 九、文档完成自检清单

- [x] 每个端点的请求参数表（参数名/位置/类型/必填/默认值/校验/业务含义）
- [x] 至少 1 个非法示例（`POST /admin/partners` 缺 `lookup`；`amount<=0`）
- [x] 每个返回体的字段表（含嵌套展开与业务含义）
- [x] 空数据示例（`items: []`）
- [x] 业务规则（服务红娘排除、自动入团、软删除、团队变更、余额记账、比例必填）
- [x] 错误码表（HTTP + 触发条件 + 前端处理 + JSON 示例）
- [x] 与代码（`app/api/routes/partner_admin.py`、`app/api/routes/partner_level_admin.py`、`app/services/partner_admin.py`、`app/services/partner_level_admin.py`、`app/schemas/partner_admin.py`、`app/schemas/partner_level_admin.py`）一致

## 十、变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-09-12 | v1 | 新建：合伙人管理 8 + 团队关系 3 + 分成明细 3 + 分成配置 3 = 共 17 端点；新建 `partner_level_config` 表；`partner_team` 补 `level_id`；`commission_entry` 支持手工录入 | M6 后端首版契约 |
