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

`GET /admin/matchmaker/members/{member_id}` 现在返回 `self_intro`、`ideal_partner`、`wechat`、`last_login_at`、`ip_location`（来源于注册 IP）。`PATCH /admin/matchmaker/members/{member_id}` 支持更新 `self_intro`、`ideal_partner`、`wechat`，字段为空时可传 `null` 清空。

## GET `/admin/members/{member_id}/call-records`

分页查询会员通话记录，返回 `id,user_id,direction,status,duration_seconds,remark,created_by,created_at`。记录存储在 `member_call_record`，没有记录时返回空列表。

## POST `/admin/members/{member_id}/call-records`

新增通话记录。Body：`{"direction":"OUTBOUND","status":"COMPLETED","duration_seconds":60,"remark":"已沟通"}`；`direction` 支持 `INBOUND/OUTBOUND`，`status` 支持 `COMPLETED/MISSED/FAILED`。

## 变更记录

- 2026-08-25：新增上述全局 CRM 列表和批量状态接口，供会员跟进、线上行为、会员认证和列表批量操作页面使用。
- 2026-08-25：补齐会员详情自我介绍、择偶要求、微信和登录信息字段；新增会员通话记录表及读写接口。
