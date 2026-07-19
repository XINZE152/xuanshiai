# 注册身份与红娘申请 API

统一前缀：`/api/v1`。除查询身份选项和红娘申请类型外，接口使用：
`Authorization: Bearer <access_token>`。

## 查询注册身份选项

`GET /api/v1/registration/intents`

响应：

```json
[
  {"intent_type":"self_match","label":"自己找","description":"以本人交友和婚恋匹配为主要目的"},
  {"intent_type":"parent_match","label":"父母帮找","description":"由本人授权父母参与资料和匹配流程"},
  {"intent_type":"companion","label":"找搭子","description":"以兴趣、活动和同城搭子为主要目的"}
]
```

## 提交或修改注册身份

`PUT /api/v1/auth/registration-intent`

请求头：`Authorization: Bearer <access_token>`。

请求：

```json
{"intent_type":"self_match","source":"register"}
```

`intent_type` 可选 `self_match`、`parent_match`、`companion`。接口是幂等的，当前版本允许重新选择，后续如需限制修改应增加冷却期或人工审核规则。

## 查询当前注册身份

`GET /api/v1/auth/registration-intent`

未选择身份时返回 `null`。

## 查询红娘申请类型

`GET /api/v1/matchmaker/application-types`

响应：

```json
[
  {"application_type":"promoter","label":"推广红娘"},
  {"application_type":"partner","label":"合伙人招募"},
  {"application_type":"service_matchmaker","label":"服务红娘"}
]
```

## 提交红娘申请

`POST /api/v1/matchmaker/applications`

请求头：`Authorization: Bearer <access_token>`。

请求：

```json
{
  "application_type":"service_matchmaker",
  "real_name":"张三",
  "phone":"13800138000",
  "intro":"有多年婚恋咨询和沟通经验",
  "cert_images":["/storage/application/cert-1.jpg"]
}
```

申请人必须已绑定手机号并完成实名认证。每个用户可以分别申请不同类型；同一类型存在待审核、已通过或已暂停申请时不能重复提交。身份证、Token 等敏感信息不会出现在响应中。

## 查询我的红娘申请

`GET /api/v1/matchmaker/applications/mine`

返回当前用户全部申请，申请状态：`0`待审核、`1`通过、`2`驳回、`3`暂停。

## 管理员审核红娘申请

`PATCH /api/v1/admin/matchmaker/applications/{application_id}`

请求头：`Authorization: Bearer <admin_access_token>`。

请求：

```json
{"status":1,"fail_reason":null}
```

`status`：`1`通过、`2`驳回、`3`暂停。驳回或暂停时必须填写 `fail_reason`。审核通过后服务端授予对应平台角色，普通用户不能调用该接口。

## 错误码

| HTTP | 场景 |
| --- | --- |
| `401` | 未登录或登录会话失效 |
| `403` | 未实名、未绑定手机号或无管理员权限 |
| `404` | 申请不存在 |
| `409` | 身份或同类型申请状态冲突 |
| `422` | 身份类型、手机号、申请材料或审核参数不合法 |
