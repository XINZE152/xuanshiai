# 总店红娘 → 分成明细 后台 API

> **归属页面**：总店红娘 → 分成明细
> **URL**：前端 `xuanshiai.com/admin/love-matchmaker-distribution-details`
> **页面结构**：通知卡片 + 「录入一笔分成」按钮 + 筛选区（选择门店 / 选择红娘 / 选择事件 / 开始日期 / 结束日期）+ 明细表格（ID / 门店 / 时间 / 红娘 / 消费会员 / 分成·奖励事件 / 消费金额 / 分成金额）+ 分页
> **后端统一前缀**：`/api/v1/admin/finance/commission-entries`（复用一期 finance 模块）
> **权限点**：`finance.read`（读）
> **数据表**：`commission_entry`（订单分成明细，`beneficiary_type = 'service_matchmaker'` 表示总店红娘分成行）

---

## 一、通用约定

- **登录要求**：所有端点都需要红娘后台登录态（`Authorization: Bearer <access_token>`），未登录返回 `401`。
- **数据范围**：本组接口只返回 `beneficiary_type = 'service_matchmaker'` 的行（页面为**总店红娘**线上分成明细，总店门店列固定为「总店」）。
- **字段策略**（页面 8 列的取数口径）：
  - **门店**：本模块固定 `"总店"`（数据表无门店列；总店红娘的分成行统一归属总店）。
  - **红娘**：`commission_entry.beneficiary_id` → LEFT JOIN `users` 取 `nickname / avatar`。
  - **消费会员**：`commission_entry.order_id` → LEFT JOIN `payment_order` 取 `user_id` → 再 LEFT JOIN `users` 取 `nickname / phone / avatar`。
  - **分成/奖励事件**：`commission_entry.rule_id` → LEFT JOIN `commission_rule.name`；订单无 rule 时回退到 `payment_order.product_name`。
  - **消费金额**：`commission_entry.base_amount`（订单可分成基数，Decimal）。
  - **分成金额**：`commission_entry.amount`（Decimal）。
- **金额序列化**：`base_amount / amount` 等数值一律返回**字符串**（如 `"199.00"`），前端用 `Number()` 解析展示，避免 JS 精度丢失。
- **时间口径**：`created_at` 由 MySQL `CURRENT_TIMESTAMP` 写入（UTC），前端按浏览器时区解析显示；日期筛选按「开始日期 00:00:00 ≤ created_at < 结束日期+1 天」闭开区间匹配。
- **分页**：统一返回 `items / page / page_size / total / has_more`，排序为 `created_at DESC, id DESC`。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/finance/commission-entries` | 分页查询总店红娘线上分成明细（页面主数据） | finance.read |
| 2 | GET | `/api/v1/admin/finance/commission-entries/options` | 一次性取筛选下拉（红娘列表 + 事件列表） | finance.read |

> 注：`POST /api/v1/admin/finance/commission-entries/{entry_id}/release`（释放待结算分成）为一期已有接口，见 `docs/api/finance*.md`，此处不重复说明。

---

## 三、详细契约

### 3.1 `GET /api/v1/admin/finance/commission-entries`

**基本信息**
- 用途：分页查询总店红娘线上分成明细，支持按红娘、事件、日期区间筛选。
- Method / URL：`GET /api/v1/admin/finance/commission-entries`（无请求体）。
- 权限：`finance.read`；成功 `200`。

**请求参数（query）**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验与含义 |
| --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | 页码，`≥ 1` |
| `page_size` | query | int | 否 | 20 | 每页条数，`1 ~ 100` |
| `matchmaker_id` | query | int | 否 | — | 红娘 `users.id`，筛出该红娘的分成行 |
| `rule_id` | query | int | 否 | — | `commission_rule.id`，筛出该事件/规则的分成行 |
| `start_date` | query | string | 否 | — | 开始日期 `YYYY-MM-DD`，匹配 `created_at >= 'YYYY-MM-DD 00:00:00'` |
| `end_date` | query | string | 否 | — | 结束日期 `YYYY-MM-DD`，匹配 `created_at < 次日 00:00:00`（含当天） |

**请求示例**

```http
GET /api/v1/admin/finance/commission-entries?page=1&page_size=20&matchmaker_id=1001&rule_id=5&start_date=2026-08-01&end_date=2026-08-31
Authorization: Bearer <access_token>
```

无请求体。非法示例：`start_date=2026/08/01`（格式不符返回 `422`）。

**返回 200** — `CommissionEntryDetailPage`

```json
{
  "items": [
    {
      "id": 74,
      "created_at": "2026-08-15T10:23:38",
      "store_name": "总店",
      "matchmaker_id": 1001,
      "matchmaker_name": "王红娘",
      "matchmaker_avatar": "https://cdn.example.com/avatar/m1.png",
      "consumer_id": 88,
      "consumer_name": "会员小美",
      "consumer_phone": "138****1234",
      "consumer_avatar": null,
      "event_name": "会员爆灯",
      "beneficiary_type": "service_matchmaker",
      "order_id": 2031,
      "order_no": "XS20260815103012",
      "consumer_amount": "299.00",
      "commission_amount": "59.80",
      "status": "PENDING"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

**返回字段**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `items` | array | 是 | 明细行数组，空列表时为空数组 `[]` |
| `items[].id` | int | 是 | `commission_entry.id`，页面「ID」列 |
| `items[].created_at` | datetime(string) | 是 | 分成行创建时间（订单支付/结算成功时间），页面「时间」列 |
| `items[].store_name` | string | 是 | 固定为 `"总店"`（页面「门店」列） |
| `items[].matchmaker_id` | int | 是 | 红娘 `users.id` |
| `items[].matchmaker_name` | string | 是 | 红娘昵称；无昵称时后端生成 `红娘#{id}`（页面「红娘」列） |
| `items[].matchmaker_avatar` | string? | 是 | 红娘头像 URL，可能为 `null` |
| `items[].consumer_id` | int | 是 | 消费会员 `users.id`；订单缺失时为 `0` |
| `items[].consumer_name` | string | 是 | 会员昵称；缺失时 `用户#{id}` / `未知会员`（页面「消费会员」列） |
| `items[].consumer_phone` | string? | 是 | 会员手机号（**已由前端按业务要求掩码展示，本字段原样透传**），可能为 `null` |
| `items[].consumer_avatar` | string? | 是 | 会员头像 URL，可能为 `null` |
| `items[].event_name` | string | 是 | 分成/奖励事件名（`commission_rule.name`，回退 `payment_order.product_name`），页面「分成/奖励事件」列 |
| `items[].beneficiary_type` | string | 是 | 固定为 `service_matchmaker` |
| `items[].order_id` | int | 是 | 来源订单 `payment_order.id` |
| `items[].order_no` | string? | 是 | 来源订单号，可能为 `null` |
| `items[].consumer_amount` | string(decimal) | 是 | 消费金额（元，=`base_amount`），页面「消费金额」列 |
| `items[].commission_amount` | string(decimal) | 是 | 分成金额（元，=`amount`），页面「分成金额」列 |
| `items[].status` | string | 是 | 分成状态：`PENDING` 待结算 / `AVAILABLE` 可提现 / `FROZEN` 冻结 / `REVERSED` 冲正 |
| `page` / `page_size` / `total` / `has_more` | int/bool | 是 | 分页元信息：当前页 / 每页条数 / 总行数 / 是否还有下一页 |

**使用方法与业务规则**
- 前置条件：登录态 + `finance.read` 权限；分成行由订单支付成功后的结算流程自动写入（`mark_order_paid_and_settle`），本接口只读。
- 时间筛选为左闭右开：选 `start_date=2026-08-01&end_date=2026-08-31` 得到 8 月 1 日 00:00:00 至 8 月 31 日 23:59:59 的全部记录。
- 空结果：返回 `items: []`、`total: 0`、`has_more: false`。
- 前端处理建议：金额字段 `Number(row.consumer_amount)` 后 `toFixed(2)` 展示；`status` 可用颜色标签区分（PENDING 橙色、AVAILABLE 绿色、REVERSED 灰）。

**错误**

| HTTP | 触发条件 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录 / 令牌失效 | `{"detail":"请先登录"}` | 跳转登录 |
| 403 | 无 `finance.read` 权限 | `{"detail":"无权访问"}` | 提示无权限 |
| 422 | 参数格式错误（日期/范围） | `{"detail":[...]}` | 按校验提示修正 |

---

### 3.2 `GET /api/v1/admin/finance/commission-entries/options`

**基本信息**
- 用途：一次性返回页面筛选下拉所需的**红娘列表**（当前产生过总店红娘分成行的红娘，按 `id DESC`）与**事件列表**（启用中的 `commission_rule`，按 `beneficiary_type, priority DESC, id DESC`）。
- Method / URL：`GET /api/v1/admin/finance/commission-entries/options`（无请求体、无 query）。
- 权限：`finance.read`；成功 `200`。

**返回 200** — `CommissionEntryDetailOptions`

```json
{
  "matchmakers": [
    { "id": 1001, "name": "王红娘", "avatar": "https://cdn.example.com/avatar/m1.png" },
    { "id": 1002, "name": "李红娘", "avatar": null }
  ],
  "events": [
    { "id": 5, "name": "会员爆灯", "beneficiary_type": "service_matchmaker" },
    { "id": 7, "name": "VIP会员开通", "beneficiary_type": "service_matchmaker" },
    { "id": 9, "name": "活动报名分成", "beneficiary_type": "store" }
  ]
}
```

**返回字段**

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `matchmakers` | array | 是 | 红娘下拉选项；无数据为空数组 |
| `matchmakers[].id` | int | 是 | 红娘 `users.id`，作为筛选参数 `matchmaker_id` 提交 |
| `matchmakers[].name` | string | 是 | 红娘昵称（缺省 `红娘#{id}`） |
| `matchmakers[].avatar` | string? | 是 | 头像 URL，可空 |
| `events` | array | 是 | 事件下拉选项；无数据为空数组 |
| `events[].id` | int | 是 | `commission_rule.id`，作为筛选参数 `rule_id` 提交 |
| `events[].name` | string | 是 | 事件名（页面「分成/奖励事件」筛选项） |
| `events[].beneficiary_type` | string | 是 | 规则适用对象（`service_matchmaker/store/promoter/partner`） |

**使用方法与业务规则**
- 前置条件：登录态 + `finance.read` 权限。
- 页面加载时调用一次并缓存到内存；切换分页/搜索时**不必**重复拉取 options。
- 红娘下拉只列出「产生过分成行」的红娘（避免列出从未有分成的空红娘），符合明细页场景。

**错误**

| HTTP | 触发条件 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录 / 令牌失效 | `{"detail":"请先登录"}` | 跳转登录 |
| 403 | 无 `finance.read` 权限 | `{"detail":"无权访问"}` | 提示无权限 |

---

## 四、测试与验证

单测：`tests/test_commission_entry_admin.py`（OpenAPI 注册 2 端点 / 未登录 401 / query 参数在 OpenAPI 中存在性校验）。

```bash
cd E:\houduan\xuanshiai
python -m pytest tests/test_commission_entry_admin.py -q
```

关联一期接口文档：`docs/api/` 下 finance 相关契约（commission-rules / orders / ledger / withdrawals）。
