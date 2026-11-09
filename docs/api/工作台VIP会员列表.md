# 工作台 VIP 会员列表

任务：VIP-TABS-20261109。管理员端线上 VIP `/admin/members/vip`、线下 VIP `/admin/offline-vips` 使用独立后台 JWT；小程序使用以下用户身份接口，复用订阅和签约数据来源，不交换或借用后台令牌。

## 基本信息

`GET /api/v1/matchmaker/workbench/members`，需要用户登录及有效服务红娘工作台身份（`require_workspace`）。请求无 Body，响应 `application/json`，成功 HTTP 200。此接口只读，不产生购买、扣费、合同、归属或资料审核变更。

## 请求参数

| 参数 | 位置 / 类型 | 必填 / 默认 | 校验与含义 | 示例 |
| --- | --- | --- | --- | --- |
| Authorization | header / string | 是 | Bearer 用户 JWT，不能传后台 JWT | `Bearer <用户令牌>` |
| page | query / integer | 否 / 1 | 1–1000，页码 | `1` |
| page_size | query / integer | 否 / 20 | 1–100，每页会员数 | `30` |
| member_pool | query / string | 否 / null | 仅允许 `all`；传入时使用已有 CRM 全部未软删会员范围，否则保持当前红娘工作范围 | `all` |
| filters | query / string | 否 / null | 最长 4096，URL 编码 JSON 对象，键和值均为字符串；与资料条件取交集 | `{"online_vip":"1"}` |
| search | query / string | 否 / null | 最长 64；昵称、会员 ID、姓名、手机号模糊搜索，电话忽略连字符；不在响应中暴露姓名/电话 | `测试会员` |
| review_status | query / string | 否 / null | `PENDING` 待审、`PASSED` 通过、`REJECTED` 驳回；VIP 标签不自动设置审核条件 | `PASSED` |
| abandoned | query / boolean | 否 / false | 为 true 时再限制为有效会员弃海记录 | `false` |

`filters.online_vip="1"`：存在 `user_membership.status <> 3` 的订阅记录，与管理员线上 VIP 默认查询一致，包含已过期订阅。

`filters.offline_vip="1"`：存在 `offline_vip.deleted_at IS NULL` 的签约记录，与管理员线下 VIP 默认查询一致，不按服务期限、进度或在线状态排除会员。

两字段缺省或空字符串表示不限；非空只允许字符串 `"1"`。同时传入时取交集。其余资料条件沿用 [红娘后台接口 §5.5](../红娘后台接口.md) 中的 `gender/ageRange/heightRange/weight/education/occupation/income/hometown/residence/household/housing/vehicle/ethnicity/smoking/drinking/religion/mbti/marital/mine` 定义。

## 请求示例

```http
GET /api/v1/matchmaker/workbench/members?member_pool=all&page=1&page_size=30&filters=%7B%22offline_vip%22%3A%221%22%7D
Authorization: Bearer <用户令牌>
Accept: application/json
```

无请求体。线上标签将 filters 改为 URL 编码的 `{"online_vip":"1"}`；搜索附加 `search`。非法例：`filters={"offline_vip":true}`、`filters={"offline_vip":"0"}` 或 `page=0` 返回 422。

## 返回参数

| 字段 | JSON 类型 / 必返 | 含义与空值 |
| --- | --- | --- |
| items | array / 是 | 当前页会员资料卡，无匹配时 `[]` |
| page / page_size | integer / 是 | 当前页码与分页大小 |
| total | integer / 是 | 应用范围、VIP、搜索、资料条件后的去重会员总数 |
| has_more | boolean / 是 | `page * page_size < total` |
| filter_version | integer / 是 | 本版为 3；2 支持线上 VIP，3 支持线下 VIP及姓名搜索 |
| member_pool_version | integer / 是 | 1，支持 `member_pool=all` |
| abandonment_pool_version | integer / 是 | 1，支持会员弃海池 |
| items[].user_id | integer / 是 | 会员 ID，查看资料和操作使用此 ID，不是订阅或合同 ID |
| items[].nickname | string / 是 | 会员昵称，缺失时未命名会员 |
| items[].avatar | string或null / 是 | 头像资源，null 表示未设置 |
| items[].gender | integer或null / 是 | 1 男、2 女，null 未填 |
| items[].birthday | string或null / 是 | `YYYY-MM-DD`，null 未填 |
| items[].hometown / residence | string或null / 是 | 家乡 / 现居，缺失为 null；优先文本，空时由地区编码整理 |
| items[].education / job | string或null / 是 | 学历 / 职业；null 未填，沿用工作台资料口径 |
| items[].is_married | integer或null / 是 | 1 未婚、2 离异、3 丧偶、4–7 离异细分；null 未填 |
| items[].height | integer或null / 是 | 身高 cm，null 未填 |
| items[].realname_status | integer或null / 是 | 实名状态，2 为通过，null 未填 |
| items[].matchmaker_id / matchmaker_name | integer或null / string或null / 是 | 当前跟进红娘 ID / 昵称，未分派为 null |
| items[].profile_completion_score | number / 是 | 资料完整度，未记录时 0 |
| items[].review_status | string / 是 | `PENDING/PASSED/REJECTED/STORE_VERIFIED`，未审核记录按 PENDING |
| items[].review_reason | string或null / 是 | 审核原因，未记录为 null |
| items[].abandoned / abandoned_reason | boolean / string或null / 是 | 是否在弃海池及原因，无有效记录为 false / null |
| items[].reviewed_at / next_follow_at | string或null / 是 | ISO 日期时间，未审核 / 未安排时 null |
| items[].created_at | string / 是 | ISO 注册日期时间 |

## 返回示例

```json
{"items":[{"user_id":11,"nickname":"测试会员","avatar":null,"gender":2,"birthday":"1996-01-01","hometown":null,"residence":null,"education":null,"job":null,"is_married":1,"height":165,"realname_status":0,"matchmaker_id":null,"matchmaker_name":null,"profile_completion_score":0,"review_status":"PENDING","review_reason":null,"abandoned":false,"abandoned_reason":null,"reviewed_at":null,"next_follow_at":null,"created_at":"2026-01-01T00:00:00"}],"page":1,"page_size":30,"total":1,"has_more":false,"filter_version":3,"member_pool_version":1,"abandonment_pool_version":1}
```

空结果：`{"items":[],"page":1,"page_size":30,"total":0,"has_more":false,"filter_version":3,"member_pool_version":1,"abandonment_pool_version":1}`。

## 使用方法与业务规则

先使用当前环境用户账号登录并取得工作台准入，再请求列表。此处复用既有列表读取权限；详情、联系、分派、审核、编辑分别继续校验自身权限与归属。未传 `member_pool=all` 时 VIP 条件不能扩大工作范围。

VIP 条件使用 EXISTS，在计数和分页前完成筛选。同一会员的多次续费只产生一张资料卡，因此 total 是会员数，与管理员按开通记录计数可能不同。分页按会员 ID 倒序；切换标签、关键词或筛选时重置页码。加载更多沿用当前所有条件。软删签约和作废订阅不匹配；期限已过的记录仍匹配；没有匹配结果返回空列表，不能补入广场或演示会员。

请求可重复，无幂等键、扣次或新增专用限流；沿用全局频率限制。并发读不锁定数据快照，期间签约/删除可使总数变化，刷新第一页取得最新列表。线上标签须检查 `filter_version>=2`，线下须检查 `>=3`；缺版本或旧版本应提示后端更新，不把旧响应当成 VIP 数据。

## 错误

| HTTP | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录、用户令牌无效或过期 | 沿用续期 / 重新登录 |
| 403 | 没有有效服务红娘工作台身份 | 展示无权限，禁止回落管理员接口 |
| 422 | 非法页码、枚举、筛选键值或 JSON；旧服务不认识 VIP 条件 | 展示错误；旧版明确提示后端升级 |

筛选非法示例：`{"detail":"无效的筛选条件：offline_vip"}`；结构非法示例：`{"detail":"筛选条件格式不正确"}`。框架参数校验返回 `{"detail":[{"loc":["query","page"],"msg":"Input should be greater than or equal to 1","type":"greater_than_equal","input":"0","ctx":{"ge":1}}]}`。此接口无独立业务错误码，以 HTTP 和 detail 识别；网络失败允许重试。

## 变更记录

- 2026-11-09 / VIP-TABS-20261109：新增 `filters.offline_vip="1"`，filter_version 从 2 升为 3，搜索增加姓名。保留线上订阅筛选及所有旧字段、路径、认证与列表范围，向后兼容，无字段废弃或数据库迁移。
- 小程序两个 VIP 标签统一使用本接口；取消线下标签原有广场在线状态筛选、仅首页20条限制，支持服务端翻页。远端须部署此筛选实现后才能完成联调。
