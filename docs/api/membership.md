# 会员与微信支付接口

会员只有 VIP 一种等级，套餐由 `config_membership_package` 的上架数据决定。支付方式固定微信支付，服务端决定价格、有效天数和权益。

## 接口

| Method | URL | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/v1/membership/packages` | 无 | 查询在售套餐 |
| GET | `/api/v1/users/me/membership` | 登录 | 当前有效期和权益 |
| GET | `/api/v1/users/me/membership/history?page=1&page_size=20` | 登录 | 会员历史分页 |
| POST | `/api/v1/membership/orders` | 登录 | 创建待支付微信订单 |
| GET | `/api/v1/membership/orders/{order_no}` | 订单本人 | 查询订单 |
| POST | `/api/v1/payments/wechat/callback` | 微信服务端 | 支付回调边界 |

会员套餐可以查询，但会员购买接口当前固定返回 `403`（`会员购买功能暂未开放`），因此不会创建订单、扣款或开通会员。支付能力和微信商户配置完成后再开放购买。

当前会员权益：普通用户每日申请 3 次、爆灯 1 次、浏览 20 次；VIP 每日申请 10 次、爆灯 3 次、普通浏览不限、可看历史浏览和访客详情。会员不能绕过手机号、资料完整、实名认证、隐私、拉黑和封禁规则。

微信回调在商户 API v3 密钥和验签适配器配置前返回 `503`，不会把前端请求或未验签回调标记为支付成功，也不会发放会员。
