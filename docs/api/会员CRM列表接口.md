# 会员 CRM 列表接口

统一前缀为 `/api/v1`，所有接口需要红娘后台 `Authorization: Bearer <access-token>`，响应为 JSON。

## GET `/admin/members/follow-ups`

分页查询全部会员跟进记录。Query：`page`（整数，默认 1）、`page_size`（1-100，默认 20）、`search`（可选，昵称或手机号，最多 64 字符）。成功返回 `{items,page,page_size,total,has_more}`；每条 item 含 `id,user_id,nickname,method,content,next_follow_at,created_by,created_at`。未登录返回 401，参数非法返回 422。

## GET `/admin/members/behavior/all`

分页查询全量会员线上行为。Query 与跟进列表相同。item 含 `event_type,event_id,user_id,nickname,target_user_id,target_nickname,detail,occurred_at`，事件来自登录、浏览、收藏、滑动和认识申请流水；无记录返回空 `items`。

## GET `/admin/matchmaker/members/auth`

分页查询认证汇总。Query：`page`、`page_size`、可选 `auth_status`（0 未认证、1 审核中、2 已通过、3 未通过）和 `search`。item 含 `id,nickname,phone,gender,birthday,real_name,id_card,auth_status,submitted_at`。身份证等敏感字段仅对已授权后台返回。

## POST `/admin/matchmaker/members/batch-status`

批量修改会员状态。Body：`{"member_ids":[1,2],"status":1,"reason":"会员列表批量修改"}`；`member_ids` 必须 1-200 个正整数，`status` 为 1/2/3，`reason` 1-255 字符。成功返回 `{"updated":2,"status":1}`，事务内完成更新；未登录 401，参数非法 422。重复提交是幂等的，不会重复创建会员。

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
| `auth_status` / `intention_level` | integer/null | `auth_status` 为 0-3；`intention_level` 为 1-3 | 审核状态、客户意向 |

**请求示例**：

```http
PATCH /api/v1/admin/matchmaker/members/123
Authorization: Bearer <access-token>
Content-Type: application/json

{"nickname":"小林","height":175,"education":"本科","school":"浙江大学","company":"某科技公司","match_status":1}
```

**返回示例**：返回会员摘要 `{id,nickname,phone_masked,gender,status,is_vip,vip_end_at,matchmaker_id,created_at,updated_at}`。敏感手机号仅返回脱敏值；实名姓名和身份证不属于该接口可编辑字段。

**业务规则与错误**：操作在事务中完成并记录审计日志；资料写入 `users`、`user_profile`、`user_auth` 和 `user_privacy` 的既有归属表，重复提交同一值无副作用。会员不存在返回 404，枚举、日期、长度或范围非法返回 422，未登录返回 401，数据库缺少兼容字段返回 503。旧客户端不传新增字段即可继续使用。

### PATCH `/admin/matchmaker/members/{member_id}/assignment`

**用途与权限**：修改会员的服务红娘分派。需要红娘后台管理员登录，成功返回 200；`matchmaker_id` 可为有效服务红娘用户 ID，传 `null` 取消当前分派。

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

## 变更记录

- 2026-09-16：`PATCH /admin/matchmaker/members/{member_id}` 扩展会员基本资料更新字段，新增星座、饮酒、宗教、结婚计划、学校、单位和 `match_status`；`GET /admin/matchmaker/members/{member_id}` 及会员列表同步返回这些字段。向后兼容，旧客户端无需改动。
- 2026-08-26：会员列表审核、客户意向和服务红娘下拉框接入持久化接口；新增 `auth_status`、`intention_level` 更新字段和会员分派接口。旧字段与旧调用方式保持兼容。
- 2026-08-25：新增上述全局 CRM 列表和批量状态接口，供会员跟进、线上行为、会员认证和列表批量操作页面使用。
- 2026-08-25：补齐会员详情自我介绍、择偶要求、微信和登录信息字段；新增会员通话记录表及读写接口。
