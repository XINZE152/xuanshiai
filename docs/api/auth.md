# 用户与认证 API

统一前缀：`/api/v1`。除登录、发送验证码、刷新 Token 外，接口使用
`Authorization: Bearer <access_token>`。

## 发送短信验证码

`POST /api/v1/auth/sms/send`

请求：

```json
{"phone":"13800138000","purpose":"login"}
```

`purpose` 可选 `login` 或 `bind_phone`。同一手机号 60 秒内最多发送一次，24 小时最多十次；验证码有效期 5 分钟，旧验证码在新验证码发送后失效。

响应 `202`：

```json
{"message":"验证码已发送","expires_in":300,"retry_after":60}
```

## 手机号登录

`POST /api/v1/auth/phone/login`

请求：

```json
{"phone":"13800138000","purpose":"login","code":"123456","device_id":"device-1","platform":"wechat-mini-program","app_version":"1.0.0"}
```

响应 `200`：

```json
{"access_token":"<token>","refresh_token":"<token>","token_type":"bearer","expires_in":3600,"need_bind_phone":false,"user_id":1}
```

新手机号自动创建用户；已冻结或注销账号返回 `403`。

## 微信登录

`POST /api/v1/auth/wechat/login`

请求：

```json
{"code":"wx.login-return-code","nickname":"用户昵称","avatar":"https://example.com/avatar.jpg","device_id":"device-1","platform":"wechat-mini-program","app_version":"1.0.0"}
```

服务端调用微信 `jscode2session` 换取 `openid`，同一 `openid` 永远映射到同一用户。未绑定手机号时响应中的 `need_bind_phone` 为 `true`，只能访问登录、协议、绑定手机号和资料引导相关接口。

## 绑定手机号

`POST /api/v1/auth/bind-phone`

Header：`Authorization: Bearer <access_token>`。请求体与验证码请求相同，但 `purpose` 必须为 `bind_phone`。已绑定到其他账号的手机号返回 `409`，不会覆盖原绑定。

## Token 刷新与退出

- `POST /api/v1/auth/refresh`：请求 `{"refresh_token":"<token>"}`，刷新 Token 后旧 Refresh Token 立即失效。
- `POST /api/v1/auth/logout`：撤销当前会话，幂等返回 `204`。
- `POST /api/v1/auth/logout-all`：撤销当前账号所有会话，返回 `204`。
- `GET /api/v1/auth/me`：返回脱敏手机号、账号状态和实名认证状态。

## 协议签署

`GET /api/v1/auth/agreements` 返回当前发布的协议类型和版本；`POST /api/v1/auth/agreements/accept` 记录签署。

`POST /api/v1/auth/agreements/accept`

请求：`{"agreement_type":"safety_pledge","version":"v1","content_hash":"<sha256>","scene":"profile"}`。版本必须与服务端当前发布版本一致，记录用户、版本、时间、IP、设备和场景。

## 实名认证

`POST /api/v1/auth/realname`

请求：`{"real_name":"张三","id_card":"110101199001011234"}`。服务端校验身份证格式和年满 18 周岁，身份证原文加密保存，提交后进入审核中：`{"status":1,"id_card_masked":"1101**********1234"}`。一期不伪造第三方认证成功；接入二要素服务后由认证适配器更新为已认证（`status=2`）。

## 个人资料

- `GET /api/v1/users/me/profile`
- `PATCH /api/v1/users/me/profile`
- `GET /api/v1/users/me/completion`

出生日期由服务端计算年龄且必须满 18 周岁；性别首次提交后不可自行修改。完整的资料、媒体、择偶要求和主页预览接口见 [`docs/api/profile.md`](profile.md)。

签署当前版本的 `safety_pledge` 协议后，会同步记录用户的单身承诺完成状态。

## 错误码

| HTTP | 场景 |
| --- | --- |
| `400` | 验证码错误、过期或微信凭证无效 |
| `401` | Token 无效、过期或会话已撤销 |
| `403` | 账号冻结/注销或权限不足 |
| `409` | 手机号冲突、协议版本过期、认证信息不可修改 |
| `422` | 请求格式、长度、范围或成年人校验失败 |
| `429` | 验证码频率、次数或错误锁定限制 |
| `503` | 微信或短信服务未配置/不可用 |
