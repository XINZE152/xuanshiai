# M8 运营工具 接口契约

> 本批次覆盖运营工具模块的全部 22 个页面。所有页面均走通用 `admin_content` CRUD + 配置域 `tools_*` 存储前端表单字段。

## 1. 模块结构

| 业务域 | 前端页面 | 后端路由 | 配置域 | 通用域 |
|---|---|---|---|---|
| 红娘喜讯栏目配置 | `/xixun-menu` | — | `tools_good_news` | — |
| 红娘喜讯·喜讯管理 | `/xixun-list` | `admin/content/good_news` | — | `good_news` |
| 红娘喜讯·锦旗管理 | `/xixun-banner` | `admin/content/good_news_pennant` | — | `good_news_pennant` |
| 搭子社群·栏目配置 | `/group-menu` | — | `tools_column_config` | — |
| 搭子社群·社群管理 | `/group-list` | `admin/content/community_group` | — | `community_group` |
| 搭子社群·报名管理 | `/group-signup` | `admin/content/group_signup` | — | `group_signup` |
| 互动消息·功能设置 | `/interact-config` | — | `tools_interactive_function` | — |
| 互动消息·内容设置 | `/interact-content` | — | `tools_interactive_content` | — |
| 互动消息·消息记录 | `/interact-record` | `admin/content/interactive_message` | — | `interactive_message` |
| 自由收款 | `/free-pay` | — | `tools_free_pay` + `admin/content/free_pay_order` | `free_pay_order` |
| 内容单页 | `/single-page` | `admin/content/single_page` | — | `single_page` |
| 积分商城·礼品管理 | `/gift-list` | `admin/content/gift` | — | `gift` |
| 积分商城·兑换管理 | `/gift-exchange` | `admin/content/gift_exchange` | — | `gift_exchange` |
| 会员分区 | `/tool-theme` | — | `tools_member_zone` | — |
| 超级获客·落地页 | `/customer-landing` | `admin/content/landing_page` | — | `landing_page` |
| 超级获客·自由表单 | `/free-form` | `admin/content/free_form` | — | `free_form` |
| 批量资料卡 | `/tool-lovecard` | `admin/content/lovecard_batch` | — | `lovecard_batch` |
| 销售匹配库 | `/sales-match` | `admin/content/sales_library` | `tools_sales_match` | `sales_library` |
| 送礼物 | `/love-gift-wrap` | `admin/content/gift` + `admin/content/gift_record` | — | `gift` / `gift_record` |
| 推文助手 | `/generate-tool` | `admin/content/tweet_task` | — | `tweet_task` |
| 吸粉二维码 | `/qrcode-wrap` | `admin/content/fan_qrcode` | — | `fan_qrcode` |
| 短信群发 | `/sms-group` | `admin/content/sms_broadcast` + `admin/content/sms_send_record` | — | `sms_broadcast` / `sms_send_record` |

## 2. 通用接口

### 2.1 内容项 CRUD（所有 admin_content 域通用）

所有运营工具页面均通过下列端点操作 `admin_content_item` 表：

```
GET    /admin/content/{domain}            列表
POST   /admin/content/{domain}            新增
PATCH  /admin/content/{domain}/{item_id}  更新
DELETE /admin/content/{domain}/{item_id}  删除
```

通用请求参数（query）：
- `page`：页码，从 1 开始，默认 1
- `page_size`：每页条数，默认 20，最大 100
- `keyword`：模糊匹配，作用于 title / subtitle / extra_json 三处
- `status`：可选 1 正常 / 2 停用

通用响应：`ContentItemPage`：

```json
{
  "items": [{
    "id": 1,
    "domain": "good_news",
    "title": "喜讯-反馈方",
    "subtitle": "红娘昵称",
    "image_url": null,
    "amount": null,
    "status": 1,
    "sort": 100,
    "extra": { "...": "业务字段 JSON" },
    "created_at": "2026-09-08 10:00:00",
    "updated_at": "2026-09-08 10:00:00"
  }],
  "total": 234,
  "page": 1,
  "page_size": 20
}
```

> 字段约束：所有业务字段都放在 `extra` JSON 中，行级只保留通用字段（标题、副标题、封面图、金额、状态、排序、时间）。

### 2.2 配置域读写（前端表单字段）

所有 `tools_*` 命名空间都通过通用平台配置接口读写：

```
GET   /admin/configs/{namespace}    读取
PUT   /admin/configs/{namespace}    保存（整体覆盖）
```

请求体为 JSON 对象（与默认值的 schema 完全一致）。

## 3. 业务字段说明（extra JSON）

### 3.1 good_news（喜讯管理）

```json
{
  "status": "牵手成功",
  "matchmaker_id": 12,
  "matchmaker_name": "李会强",
  "feedback_name": "张三",
  "partner_name": "李四",
  "feedback_source": "member",
  "partner_source": "custom",
  "meet_duration": "3",
  "content_html": "...",
  "blessing": "祝福语",
  "public": true,
  "views": 12
}
```

### 3.2 good_news_pennant（锦旗管理）

```json
{
  "maker": "用户 Fairy",
  "giver": "张三&李四",
  "target": "芸希老师",
  "words": "用心搭佳缘 温暖伴余生",
  "public": true,
  "template": "default"
}
```

### 3.3 community_group（社群管理）

```json
{
  "area": "江苏省南京市建邺区",
  "category": "美食搭子",
  "banner_url": "...",
  "tags": ["搭子", "干饭"],
  "signup_mode": "注册登录",
  "fee": 0,
  "reward": 50,
  "initiator": "发起人昵称",
  "content_html": "...",
  "status": "邀请加入",
  "signup_count": 1
}
```

### 3.4 group_signup（报名记录）

```json
{
  "group_id": 1,
  "group_title": "美食干饭搭子",
  "nick": "别吻我橘子",
  "phone": "166****8875",
  "profile": "已完善",
  "auth": "已认证",
  "signup_seq": 1,
  "pay": "free",
  "order_no": "",
  "pay_way": "",
  "promoter": ""
}
```

### 3.5 interactive_message（互动消息记录）

```json
{
  "sender_name": "秋刀鱼",
  "sender_id": "B976071",
  "sender_matchmaker": "李会强",
  "recv_name": "1196",
  "recv_id": "G617884",
  "recv_matchmaker": "荷菱颖",
  "send_at": "2026-07-09 10:44",
  "reply_at": "未回复",
  "last_at": "2026-07-09 10:44"
}
```

### 3.6 landing_page / free_form（落地页/自由表单）

参考 `customer-landing` 与 `free-form` 页面 UI 字段：
- form_type: "内置表单" / "自由表单"
- page_type: "单页" / "引导页"
- fields: ["昵称", "性别", ...]
- data_pos: "保存到客源线索" / "保存到会员资料..."
- phone_ver: "无需验证..." / "需验证..."
- button_text / button_color / redirect_url / promoter ...

### 3.7 sales_library（销售匹配库）

```json
{
  "gender": "男性",
  "acct_mode": "按昵称",
  "acct_name": "客户昵称",
  "smart": false,
  "matchmaker": "芸希老师",
  "pick_limit": 10,
  "arrival": "已到店",
  "tips": "温馨提示内容"
}
```

### 3.8 lovecard_batch（批量资料卡）

```json
{
  "status": "不限",
  "gender": "不限",
  "head_mode": "会员头像",
  "qrcode": "普通H5二维码",
  "batch": "第1-50条",
  "chips": { "marriage": ["未婚"], "edu": ["不限"], ... }
}
```

### 3.9 gift（礼物）+ gift_record（赠送记录）

礼物 extra：`{ "unit": "个", "icon": "🔮", "required": 900, "reward": 450, "sales": 3 }`
赠送记录 extra：`{ "from_name": "...", "from_id": "B634017", "to_name": "...", "to_id": "G06087", "qty": "1颗", "consume": 900, "paid": 0, "reward": 450, "pay_status": "未支付" }`

### 3.10 tweet_task（推文任务）

```json
{
  "age_min": "18",
  "age_max": "70",
  "style": "模板1",
  "qr": "普通H5二维码",
  "chips": { "gender": [], "marriage": [], ... },
  "gen_mode": "生成本页全部数据（50条/页）",
  "gen_count": "50"
}
```

### 3.11 fan_qrcode（吸粉二维码）

```json
{
  "share_title": "南京单身",
  "share_summary": "给你发一个高颜值对象",
  "share_link": "https://...",
  "validity": "临时二维码（30天后失效）" / "永久有效二维码",
  "push_count": 0,
  "follow_count": 0
}
```

### 3.12 sms_broadcast（短信群发任务）

```json
{
  "target": "所有会员",
  "tpl_id": "Q1329",
  "phone_count": 100,
  "sent_count": 95,
  "fail_count": 5
}
```

## 4. 域白名单

`app/services/admin_content.py:ALLOWED_DOMAINS` 中新增的 M8 域：

- `good_news` / `good_news_pennant`
- `community_group` / `group_signup`
- `interactive_message`
- `tweet_task`
- `sms_broadcast` / `sms_send_record`

## 5. 路由顺序铁律

所有 admin_content 静态域段（`/good_news`、`/community_group` 等）必须按字母顺序声明在动态段 `/{domain}` 之后（FastAPI 路径匹配按声明顺序）。当前路由 `admin_content.py` 已正确排序。

## 6. 字段约束

- 所有业务字段都放在 `extra` JSON 中
- `keyword` 模糊匹配同时作用于 title/subtitle/extra_json（LIKE 检索）
- Decimal 字段序列化为字符串
- 上传图片走通用 `POST /admin/common/upload`

## 7. 权限

- 列表/读取需要 `platform.config.read`
- 新增/更新/删除需要 `platform.config.write`

配置域读写需要 `platform.config.read` / `platform.config.write`。
