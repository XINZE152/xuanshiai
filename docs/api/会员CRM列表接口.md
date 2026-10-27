# 会员 CRM 列表接口

统一前缀为 `/api/v1`，所有接口需要红娘后台 `Authorization: Bearer <access-token>`，响应为 JSON。

## GET `/admin/members/follow-ups`

分页查询全部会员跟进记录。Query：`page`（整数，默认 1）、`page_size`（1-100，默认 20）、`search`（可选，昵称或手机号，最多 64 字符）。成功返回 `{items,page,page_size,total,has_more}`；每条 item 含 `id,user_id,nickname,method,content,next_follow_at,created_by,created_at`。未登录返回 401，参数非法返回 422。

## GET `/admin/members/behavior/all`

分页查询全量会员线上行为。Query 与跟进列表相同。item 含 `event_type,event_id,user_id,nickname,target_user_id,target_nickname,detail,occurred_at`，事件来自登录、浏览、收藏、滑动和认识申请流水；无记录返回空 `items`。

## GET `/admin/matchmaker/members/auth`

分页查询认证汇总。Query：`page`、`page_size`、可选 `auth_status`（0 未认证、1 审核中、2 已通过、3 未通过）和 `search`。item 含 `id,nickname,phone,gender,birthday,real_name,id_card,auth_status,submitted_at`。身份证等敏感字段仅对已授权后台返回。

## POST `/admin/matchmaker/members/batch-status`

批量改状态采用部分成功语义。响应同时返回 `updated_ids` 和 `rejected_ids`；不存在、越权或不满足作用域的会员进入 `rejected_ids`，不会被修改。`updated` 等于 `updated_ids` 的长度。

批量修改会员状态。Body：`{"member_ids":[1,2],"status":1,"reason":"会员列表批量修改"}`；`member_ids` 必须 1-200 个正整数，`status` 为 1/2/3，`reason` 1-255 字符。成功返回 `{"updated":2,"status":1}`，事务内完成更新；未登录 401，参数非法 422。重复提交是幂等的，不会重复创建会员。

> **敏感操作（2026-10-11，D-9）**：必须携带 `Idempotency-Key`（8–128）与 `confirm=true`；缺幂等键 `422`，缺二次确认 `428`。同一 `Idempotency-Key` 重放直接返回上次的 `{updated,status}`，不重复执行更新；`operation` 为 `matchmaker.member.batch_status`。

## 会员详情补充字段

`GET /admin/matchmaker/members/{member_id}` 返回会员基础资料、`self_intro`、`ideal_partner`、`wechat`、`last_login_at`、`ip_location`（来源于注册 IP）以及交友展示状态 `match_status`。`PATCH /admin/matchmaker/members/{member_id}` 支持更新基础资料字段，字段为空时可传 `null` 清空。

## 会员列表快捷修改

### PATCH `/admin/matchmaker/members/{member_id}`

**用途与权限**：红娘后台管理员修改会员基础资料、认证状态或客户意向。需要 `Authorization: Bearer <access-token>`，`Content-Type: application/json`，成功返回 200。新增字段均为可选，旧客户端不传即可保持原有行为。

**请求参数**：`member_id` 为 path 正整数且必须存在。Body 至少传一个字段，其他字段不受影响。

| 字段 | 类型 | 校验 | 含义 |
| --- | --- | --- | --- |
| `nickname` | string/null | 1-64 字符 | 会员昵称 |
| `gender` | integer/null | `1` 男、`2` 女 | 性别 |
| `birthday` | string/null | `YYYY-MM-DD` | 生日 |
| `height` / `weight` | integer/null | 身高 80-250、体重 40-120 | 身高厘米、体重千克 |
| `constellation` / `zodiac` | string/null | 最多 16 字符 | 星座、属相 |
| `hometown` / `residence` / `household` | string/null | 最多 128 字符 | 家乡、现居、户籍展示名称 |
| 区域代码字段 | string/null | 最多 32 字符 | 家乡、现居、户籍的省/市/区代码：`*_province_code`、`*_city_code`、`*_district_code` |
| `education` / `school` / `job` / `company` | string/null | 64/128 字符上限 | 学历、学校、职业、单位 |
| `income` | number/null | 0-1000000 | 月收入数值 |
| `ethnicity` / `house` / `car` / `smoking` / `drinking` / `religion` | string/null | 最多 32 字符 | 民族、购房、购车、吸烟、饮酒、宗教 |
| `marriage_plan` | string/null | 最多 64 字符 | 结婚计划 |
| `match_status` | integer/null | `1-5` | 交友展示状态：公开相亲、委托红娘、完全私密、停止相亲、已经脱单 |
| `tags` | array/object/null | 标签数组或分类标签对象 | 会员标签 |
| `auth_status` / `intention_level` | integer/null | `auth_status` 为 0-3；`intention_level` 为 1-3（1低 2中 3高） | 审核状态、客户意向 |
| `wechat` | string/null | 最多 128 字符 | 微信号；提交掩码值（含 `***`）会被丢弃，不会覆盖真实号 |
| `wechat_added` | integer/null | `0` 未添加、`1` 已加上、`2` 没加上 | 加微信结果 |
| `promoted` / `newcomer` | boolean/null | `true` / `false` | 超级管理的「置顶推荐」「新人推荐」开关，写入 `users.promoted` / `users.newcomer`（库内为 tinyint，服务端转 1/0） |
| `phone` | string/null | 正则 `^1\d{10}$` | 手机号。登录标识，全局唯一；与其它会员重复返回 409 |

**请求示例**：

```http
PATCH /api/v1/admin/matchmaker/members/123
Authorization: Bearer <access-token>
Content-Type: application/json

{"nickname":"小林","height":175,"education":"本科","school":"浙江大学","company":"某科技公司","match_status":1}
```

**返回示例**：返回会员摘要 `{id,nickname,phone_masked,gender,status,is_vip,vip_end_at,matchmaker_id,created_at,updated_at}`。敏感手机号仅返回脱敏值；实名姓名和身份证不属于该接口可编辑字段。

**业务规则与错误**：操作在事务中完成并记录审计日志；资料写入 `users`、`user_profile`、`user_auth` 和 `user_privacy` 的既有归属表，重复提交同一值无副作用。会员不存在返回 404，枚举、日期、长度或范围非法返回 422，未登录返回 401，数据库缺少兼容字段返回 503。旧客户端不传新增字段即可继续使用。

**手机号改号的额外约束**：`phone` 是会员登录标识与唯一键。服务端先排除会员自身再查重，命中其它会员返回 `409 {"detail":"手机号已被其他会员占用"}`；格式不合 `^1\d{10}$` 返回 422。改号不迁移登录态，会员需重新登录。

### PATCH `/admin/matchmaker/members/{member_id}/assignment`

**用途与权限**：修改会员的服务红娘分派。需要红娘后台管理员登录，成功返回 200；`matchmaker_id` 可为有效服务红娘用户 ID，传 `null` 取消当前分派。`operation` 为 `matchmaker.member.reassign`。

> **敏感操作（2026-10-11，D-9）**：必须携带 `Idempotency-Key`（8–128）与 `confirm=true`；缺幂等键 `422`，缺二次确认 `428`。同键重放返回上次结果，不重复结束/创建归属。越权（会员不在数据范围内）在幂等预占之前按 `404` 拦截，不留幂等残留行。

**请求参数**：`member_id` 为 path 正整数；`matchmaker_id` 为 body 正整数或 `null`。指定的用户必须拥有有效 `service_matchmaker` 身份。

**请求示例**：

```http
PATCH /api/v1/admin/matchmaker/members/123/assignment
Authorization: Bearer <access-token>
Content-Type: application/json

{"matchmaker_id":45}
```

取消分派示例：`{"matchmaker_id":null}`。成功返回 `{"user_id":123,"matchmaker_id":45}`。系统会结束旧的有效归属并创建新的手工分派记录，整个过程在同一事务中完成并写入审计日志；会员不存在返回 404，红娘无效返回 422，未登录返回 401。

## GET `/admin/members/{member_id}/call-records`

分页查询会员通话记录，返回 `id,user_id,direction,status,duration_seconds,remark,created_by,created_at`。记录存储在 `member_call_record`，没有记录时返回空列表。

## POST `/admin/members/{member_id}/call-records`

新增通话记录。Body：`{"direction":"OUTBOUND","status":"COMPLETED","duration_seconds":60,"remark":"已沟通"}`；`direction` 支持 `INBOUND/OUTBOUND`，`status` 支持 `COMPLETED/MISSED/FAILED`。

## 弃海筛选与统计（B3 / A9）

`GET /admin/matchmaker/members` 新增 query 参数：

| 参数 | 类型 | 必填 | 校验 | 含义 |
|---|---|---|---|---|
| `abandoned` | bool | 否 | `true` / `false` | `true` 仅弃海会员；`false` 排除弃海会员；不传不限制 |

- 谓词：`EXISTS (SELECT 1 FROM member_abandonment ma WHERE ma.user_id = u.id AND ma.restored_at IS NULL)`；
  `false` 时取 `NOT EXISTS`。弃海口径以 `member_abandonment` 表有效行（`restored_at IS NULL`）为准，
  历史客源 `LOST` 已由迁移回填为 `abandon_type='lead_sync'`。
- 出参：`MemberListItem` 新增 `abandoned: bool`（默认 `false`）。
- 统计：`GET /admin/matchmaker/members/statistics` 新增 `abandoned`（默认 `0`），
  口径已按 `scope_exists_clause` 收口，门店/组织账号不会看到全局规模。

弃海列表、手动弃海与捞回接口见 [`docs/api/member-abandonment.md`](member-abandonment.md)。
批量弃海见下方「批量弃海」小节与 [`docs/api/member-abandonment.md`](member-abandonment.md)。

## 2026-10-24 缺口补全（B0/B5/B6/B8/B9/A15）

### `GET /admin/matchmaker/members` 新增查询参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|---|
| `auth_status` | query | string | 否 | 无 | 正则 `^[0-4](,[0-4]){0,4}$` | **B6**：资料审核状态，支持英文逗号分隔多值。`0`未提交 `1`审核中 `2`已通过 `3`未通过 `4`已到店核验；`1,2,3,4` = 已提交资料。单值（如 `2`）与旧行为等价，向后兼容。非法示例：`auth_status=9`、`auth_status=1;2` → 422。 |
| `source__is_null` | query | bool | 否 | 无 | `true`/`false` | **B5**：`true` 仅未填写来源（`NULL` 或空串）；`false` 仅已填写来源；不传不限制。与 `source` 等值参数二选一使用。 |
| `activity_id` | query | int | 否 | 无 | `ge=1` | **B0**：按具体活动报名筛选，命中 `activity_signup` 中该活动且未取消/未拒绝（`status IN (0,1)`）的会员。 |
| `weight_min` | query | int | 否 | 无 | `20-300` | **B9**：体重下限（kg，含），按 `user_profile.weight` 过滤。缺列时静默跳过不报错。 |
| `weight_max` | query | int | 否 | 无 | `20-300` | **B9**：体重上限（kg，含）。 |
| `next_follow_start` | query | string | 否 | 无 | `YYYY-MM-DD` | **A15**：预约下次跟进时间起（含），按 `next_follow_at` 过滤，供「预约跟进日历」的全部/最近N天/指定日期 Tab 复用。 |
| `next_follow_end` | query | string | 否 | 无 | `YYYY-MM-DD` | **A15**：预约下次跟进时间止（含当日）。 |

**请求示例**：

```http
GET /api/v1/admin/matchmaker/members?auth_status=1,2,3,4&source__is_null=true&weight_min=50&weight_max=90&activity_id=7&next_follow_start=2026-10-01&next_follow_end=2026-10-31&page=1&page_size=20
Authorization: Bearer <access-token>
```

非法示例：`auth_status=1,9` → `422`（正则不匹配）。

### `MemberListItem`（列表出参）新增展示列（B8/A12）

| 字段 | 类型 | 必返 | 空值含义 | 来源 |
|---|---|---|---|---|
| `real_name` | string/null | 否 | 未实名/缺列为 null | `user_auth.real_name` |
| `weight` | int/null | 否 | 未填写/缺列为 null | `user_profile.weight` |
| `tags` | array/object/string/null | 否 | 未填写/脏数据归一化为 null | `user_profile.tags`（JSON，非法 JSON 降级 null，不 500） |
| `last_login_at` | datetime/null | 否 | 从未登录/缺列为 null | `users.last_login_at` |
| `ip_location` | string/null | 否 | 无注册 IP/缺列为 null | `users.register_ip` |
| `points` | int | 否 | 无流水为 `0` | **A12**：积分余额，取 `user_points` 流水最新一条 `balance`（相关子查询，无新表）；`sort_by=points`/`integral` 走同一表达式 |
| `login_num` | int/null | 否 | 缺列（迁移未执行）为 `null` | **A12-补列**：累计登录次数，取 `users.login_num`（`migrations/user_login_num/20261024_01`，登录链路自增） |
| `ip_region` | string/null | 否 | 缺列为 `null`；本轮仅展示、不自动解析 | **A12-属地**：`users.ip_region`（`migrations/user_region/20261024_01`） |
| `phone_region` | string/null | 否 | 缺列为 `null` | **A12-属地**：`users.phone_region` |
| `balance` | number | 否 | 无流水为 `0` | **A12-钱包**：取 `account_ledger`（`account_type='user'`、`state='AVAILABLE'`）CREDIT−DEBIT，相关子查询、无新表；`sort_by=balance` 走同一表达式 |

所有新增列所需的 JOIN（`users u` / `user_profile p` / `user_auth ua`）本已存在，纯 SELECT 补列；迁移未执行时按缺列降级为 `NULL`，字段均可选，不会让整页 500。旧客户端忽略新增字段即可，向后兼容。

### `GET /admin/matchmaker/members/statistics` 出参新增 `source_distribution`（B5/A11）

返回数组 `[{value, label, count}]`：`value` 为原始 `source` 值（为空归一化为 `""`），`label` 为展示文案（空值显示「未填写来源」），`count` 为该来源会员数。口径按 `scope_exists_clause` 收口；`users.source` 列缺失时返回空数组，其余统计字段不受影响。前端概览页「会员登记来源占比」可直取该字段，替代此前并发多次列表倒推。

### B10 会员详情 tags 放宽

`GET /admin/matchmaker/members/{member_id}` 的 `MemberDetail.tags` 类型放宽为 `dict[str, Any] | list[Any] | str | null`。历史脏数据（如 `{"custom_categories": {}}`，值为非列表）不再触发 pydantic 校验异常导致整个详情 500；非法 JSON 统一归一化为 `null`。

### B7 兼容层男女计数修复

`GET /loveadmin/api/loveUser/getAdminIndexStatistic` 的 `maleNums` / `femaleNums` 此前恒为 `0`，现取 `dashboard` 的 `male_member_count` / `female_member_count`（同口径 scope 收口），并与 `/admin/dashboard` 的 `member_gender` 一致；同时补 `offlineVipNums`（线下VIP）、`popMatchmakerNums`（推广红娘）两项已有计数。

### 批量弃海 `POST /admin/members/batch-abandon`（A18）

与 `POST /admin/matchmaker/members/batch-status` 同构，包成一次事务 + 一份汇总审计。

**基本信息**：需 `matchmaker.member.manage`；`Content-Type: application/json`；成功 `200`。**敏感操作**：必须携带 `Idempotency-Key`（8–128）与 `confirm=true`；缺幂等键 `422`，缺二次确认 `428`；`operation` 为 `matchmaker.member.batch_abandon`，同键重放返回上次结果，不重复弃海。

**请求体**：`member_ids`（1–200 正整数，去重）、`reason`（1–500）、`abandon_type`（`manual|lead_sync|auto`，默认 `manual`）。

```http
POST /api/v1/admin/members/batch-abandon
Authorization: Bearer <access-token>
Idempotency-Key: <8-128 chars>
Content-Type: application/json

{"member_ids":[1,2,3],"reason":"批量清理","abandon_type":"manual"}
```

**返回参数**：`succeeded`（成功弃海的 member_id 数组）、`failed`（`{id, error}` 数组，逐条失败原因：`会员不存在`（越权/不存在）、`该会员已在弃海池`）。越权/不存在的 id 归入 `failed` 而非整体 4xx。

**成功示例**：`{"succeeded":[1],"failed":[{"id":2,"error":"该会员已在弃海池"},{"id":3,"error":"会员不存在"}]}`。

**业务规则**：以 `member_abandonment` 有效行（`restored_at IS NULL`）为弃海口径；写操作前对作用域内目标行加 `FOR UPDATE` 锁，防并发重复；写一份 `business_audit_log`（`action='member.batch_abandon'`，成员清单记入 `after_json`）。

## 变更记录

- 2026-10-27（A12-积分 BUG 修复）：后台「发放积分 / 扣减积分」`credit-grants`/`credit-deductions` 的写入账本由 `account_ledger`改为 C 端 `user_points`（type=5、滚动余额）。**`points` 列口径不变（仍取 `user_points` 最新 `balance`），但自此后台发放会即时拉高列表「积分」列，不再误涨「余额」列**（历史误发不回填）。`balance` 列仍取 `account_ledger`。详见 `docs/api/扣减积分接口-2026-10-24.md`。

- 2026-10-24（A12-积分排序）：`sort_by` 白名单新增 `points` 与 `integral`（两 token 同口径，均按 `user_points` 最新 `balance` 降序、`u.id` 兜底），复用列表 `points` 展示列同一表达式 `MEMBER_POINTS_EXPR`，**不新建 users 列**。修复「`sort_by=integral` 直接 422」。只读、向后兼容。
- 2026-10-24（A12-会员删除）：`GET /admin/matchmaker/members` 与会员详情在 `users.deleted_at` 列存在时默认追加 `u.deleted_at IS NULL`，排除软删会员（列缺失时不拼，向后兼容）。新增 `DELETE /members/{member_id}` 与 `POST /members/batch-delete`（软删、敏感写、作用域收口、逐条归类），完整契约见 `docs/api/会员删除与批量删除接口-2026-10-24.md`。
- 2026-10-24（A12-属地刷新）：新增 `GET /members/region-resolver`、`POST /members/refresh-ip-region`、`POST /members/refresh-phone-region`（provider 门控骨架，缺省 `disabled`→`503`，未接真实厂商，不写库）。契约见 `docs/api/一键刷新属地接口-2026-10-24.md`。
- 2026-10-24（A12-钱包 balance）：`GET /admin/matchmaker/members` 与详情出参新增 `balance`（平台余额，取 `account_ledger` 中 `account_type='user'`、`state='AVAILABLE'` 的 CREDIT−DEBIT，与 `finance.get_balance` 同口径），相关子查询、不新建表；`sort_by` 白名单新增 `balance`。只读、向后兼容。余额发放/扣减与用户钱包视图见 `docs/api/钱包余额调整接口-2026-10-24.md`。
- 2026-10-24（A12-属地 ip_region/phone_region）：`users` 新增 `ip_region` / `phone_region`（varchar(64) 可空，迁移 `migrations/user_region/20261024_01`，含 up/down/verify）；列表与详情按 `user_expr` 缺列降级输出两字段（迁移未执行为 `null`）。本轮仅补列+展示，不接 IP→属地批量解析（`ip_location` 仍为原始注册 IP）。向后兼容。
- 2026-10-24（A12-补列 login_num）：`users` 新增 `login_num`（累计登录次数，int 默认 0，迁移 `migrations/user_login_num/20261024_01`，含 up/down/verify）；登录链路（手机验证码 / 微信 / 测试密码）经 `touch_login` 在列存在时自增、缺列时静默跳过；`GET /admin/matchmaker/members` 与详情出参新增 `login_num`（缺列降级 `null`），`sort_by` 白名单新增 `login_num`（缺列时回落默认排序不报错）。向后兼容：旧客户端忽略新字段即可。
- 2026-10-24（A12-列表补字段）：`GET /admin/matchmaker/members` 与会员详情的 `MemberListItem`/`MemberDetail` 出参新增 `points`（积分余额，取 `user_points` 最新一条 `balance`，无流水为 `0`）。`last_login_at`/`ip_location` 已于同日 B8/A12 补出（列已存在）。均为只读、向后兼容，无补列/新表。
- 2026-10-24（缺口补全）：`GET /admin/matchmaker/members` 新增 `auth_status`（多值，B6）、`source__is_null`（B5）、`activity_id`（B0）、`weight_min/max`（B9）、`next_follow_start/end`（A15）；`MemberListItem` 出参补 `real_name/weight/tags/last_login_at/ip_location`（B8/A12）；`/statistics` 补 `source_distribution`（B5/A11）；`MemberDetail.tags` 放宽类型修复脏数据 500（B10）；兼容层 `getAdminIndexStatistic` 修复 `maleNums/femaleNums`（B7）；新增批量弃海 `POST /admin/members/batch-abandon`（A18）。全部向后兼容。

- 2026-10-20（B3 / A9）：会员列表新增 `abandoned` 筛选参数与出参字段，统计新增 `abandoned` 计数；新增弃海 / 捞回接口。

- 2026-10-17：`PATCH /admin/matchmaker/members/{member_id}` 新增 `phone`（11 位、全局唯一、撞号 409）与 `wechat_added`（0/1/2）。此前会员抽屉里手机号与微信号的「修改」、以及「加上了？」下拉都没有可写字段，是死控件。
- 2026-10-11（D-9 敏感操作）：`PATCH /admin/matchmaker/members/{member_id}/assignment`（改派）与 `POST /admin/matchmaker/members/batch-status`（批量改状态）纳入统一敏感操作，强制 `Idempotency-Key` + `confirm=true`，同键重放不重复执行；越权在幂等预占之前拦截。
- 2026-10-04（D-4 数据范围下沉）：会员列表、统计、详情、改派、改状态、批量改状态、会员实名列表与牵线记录全部叠加后台数据范围（`ALL/SELF/STORE/ORGANIZATION` 四档，未绑定组织 fail-closed 返回 0 条）。越权访问按 `404 会员不存在` 返回；越权写操作不落库。四档定义见 [`docs/api/后台数据范围与敏感操作-2026-10-04.md`](后台数据范围与敏感操作-2026-10-04.md)。
- 2026-10-04（附带修复）：会员列表原以非聚合子查询关联 `resource_assignment`，一人多单会产生重复行并虚增 `total`，已改为按 `user_id` 聚合；会员统计的 VIP 子查询原统计全平台 VIP，已同口径收口。
- 2026-09-16：`PATCH /admin/matchmaker/members/{member_id}` 扩展会员基本资料更新字段，新增星座、饮酒、宗教、结婚计划、学校、单位和 `match_status`；`GET /admin/matchmaker/members/{member_id}` 及会员列表同步返回这些字段。向后兼容，旧客户端无需改动。
- 2026-08-26：会员列表审核、客户意向和服务红娘下拉框接入持久化接口；新增 `auth_status`、`intention_level` 更新字段和会员分派接口。旧字段与旧调用方式保持兼容。
- 2026-08-25：新增上述全局 CRM 列表和批量状态接口，供会员跟进、线上行为、会员认证和列表批量操作页面使用。
- 2026-08-25：补齐会员详情自我介绍、择偶要求、微信和登录信息字段；新增会员通话记录表及读写接口。
- 2026-10-11（筛选参数补全）：`GET /admin/matchmaker/members` 新增 `search_type`、`source`、`region`、`xiangqin_status`、`realname`、`cert_status`、`organization_id`、`matchmaker_id`、`promoter_id`、年龄/身高区间、`marriage`/`education`/`occupation`/`income`/`residence`/`hometown`/`hukou`/`religion`/`car`/`level`/`tags` 多选、`offline_vip`/`promoted`/`newcomer`/`activity_signup`/`wechat_added`/`behavior_type`、录入与分派时间区间、`order`；`auth_status` 由 0-3 放宽为 0-4；`sort_by` 新增 `entered_at`/`assigned_at`/`fetched_at`；出参新增 `source`、`region`、`cert_status`、`wechat_added`、`promoted`、`newcomer`、`activity_signup`、`level`、`fetched_at`、`assigned_at`。全部为可选参数，不传等同历史行为，向后兼容。完整契约见 `docs/api/后台筛选参数补全-2026-10-11.md`。
