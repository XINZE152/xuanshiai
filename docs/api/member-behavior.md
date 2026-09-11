# 线上行为（M3-3）管理后台接口

> 对应前端页面：`E:\HTML\xuanshiai-admin\src\app\(admin)\love-user-behavior\page.tsx`
> 路由文件：`app/api/routes/member_behavior_admin.py`
> 服务层：`app/services/member_behavior_admin.py`
> 契约：`app/schemas/member_behavior_admin.py`

覆盖「浏览记录 / 收藏记录 / 线上爆灯 / 赠送礼物 / 网友举报」五类流水的分页查询，以及爆灯 / 礼物 / 举报三类记录的删除。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 全局前缀 | `/api/v1`（本文档路径均省略该前缀） |
| 鉴权 | `Authorization: Bearer <access-token>`，独立红娘后台 Token |
| 读权限 | `matchmaker.member.read` |
| 写权限 | `matchmaker.member.manage` |
| 成功返回 | **不包 `data` 外壳**，直接返回业务对象 |
| 分页结构 | `{ items, page, page_size, total, has_more }`（固定 5 字段） |
| 错误返回 | `{"detail": "..."}`，**无业务错误码字段** |
| 会员编号 | 统一 `CONCAT('G', LPAD(user_id, 6, '0'))`，如 `G000123` |
| 金额字段 | Decimal 一律序列化为**字符串**（如 `"9.90"`），避免 JS 精度丢失 |
| 时间字段 | ISO 8601 字符串，服务端由 MySQL `UTC_TIMESTAMP()` 写入 |

### 1.1 与既有接口的关系

`member_follow_up_admin.py` 中已存在一个早期版本 `GET /admin/members/behavior/all`（仅覆盖浏览 / 收藏 / 爆灯 / 举报四类，且字段不全，**无前端调用**）。

本模块使用独立路径 `/admin/members/behavior-events` 提供完整的五类流水（新增赠送礼物、爆灯支付字段、举报 IP 与证据图），**旧接口保持原样不动**，避免破坏既有调用方。

### 1.2 五类类别与数据源

| `category` | 页面 Tab | 数据源表 | 备注 |
|---|---|---|---|
| `browse` | 浏览记录 | `user_browse_history` | 按「发起会员 + 被浏览会员」聚合，`browse_times` 为浏览次数 |
| `favorite` | 收藏记录 | `user_favorite` | 固定筛 `type = 2`（收藏） |
| `superlike` | 线上爆灯 | `user_boost` | 含支付状态 / 支付方式 / 订单号 / 爆灯状态 |
| `gift` | 赠送礼物 | `user_gift_record` | 含礼物名 / 数量 / 单位 / 积分 / 实付 / 奖励积分 |
| `report` | 网友举报 | `user_report` | 含提交 IP / 举报原因 / 详情 / 证据图 / 处理状态 |

---

## 2. 行为流水查询

### 2.1 `GET /api/v1/admin/members/behavior-events`

查询线上行为流水（五类共用同一行结构，未使用字段为 `null`）。

#### 基本信息

| 项 | 值 |
|---|---|
| 方法 | `GET` |
| 路径 | `/api/v1/admin/members/behavior-events` |
| 权限 | `matchmaker.member.read` |
| 用途 | 前端 5 个 Tab 的列表数据 |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `page` | query | int | 否 | `1` | `1 ≤ page ≤ 1000` | 页码 |
| `page_size` | query | int | 否 | `20` | `1 ≤ page_size ≤ 100` | 每页条数 |
| `category` | query | string | 否 | `browse` | 正则 `^(browse\|favorite\|superlike\|gift\|report)$` | 行为类别，切换 Tab 时传 |
| `search` | query | string | 否 | — | 长度 ≤ 64 | 会员关键字：匹配**发起方**昵称 / 手机号 / 会员编号（`G000123` 或纯数字 `000123`） |
| `min_times` | query | int | 否 | — | `1 ≤ min_times ≤ 1000` | 浏览次数下限，**仅 `category=browse` 生效**；页面「3次以上浏览」传 `3`、「5次以上浏览」传 `5` |
| `status` | query | int | 否 | — | `0 ≤ status ≤ 2` | 举报处理状态，**仅 `category=report` 生效**：`0` 待处理 / `1` 已处理 / `2` 驳回 |
| `pay_status` | query | int | 否 | — | `0 ≤ pay_status ≤ 1` | 支付状态，**仅 `category=superlike` / `gift` 生效**：`0` 未支付 / `1` 已支付 |

> `min_times` / `status` / `pay_status` 传给了不匹配的 `category` 时**被忽略**，不报错。

#### 请求示例

合规（浏览记录，3 次以上，按编号搜 `000123`）：

```http
GET /api/v1/admin/members/behavior-events?page=1&page_size=20&category=browse&min_times=3&search=000123
Authorization: Bearer <access-token>
```

**非法示例**（`category` 不在枚举内 → `422`）：

```http
GET /api/v1/admin/members/behavior-events?category=unknown
Authorization: Bearer <access-token>
```

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["query", "category"],
      "msg": "String should match pattern '^(browse|favorite|superlike|gift|report)$'",
      "input": "unknown"
    }
  ]
}
```

**非法示例**（`page_size` 越界 → `422`）：

```http
GET /api/v1/admin/members/behavior-events?page_size=500
```

#### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
|---|---|---|---|
| `items` | array | 是 | 流水行列表 |
| `page` | int | 是 | 当前页码 |
| `page_size` | int | 是 | 每页条数 |
| `total` | int | 是 | 符合条件的总条数 |
| `has_more` | bool | 是 | 是否还有下一页（`page * page_size < total`） |

**`items[].` 通用字段**（五类共有）：

| 字段 | 类型 | 可为 null | 业务含义 |
|---|---|---|---|
| `event_id` | int | 否 | 事件主键（各来源表的 `id`；浏览类为聚合后的 `MIN(id)`） |
| `user_id` | int | 否 | 动作发起会员 `user_id` |
| `member_code` | string | 否 | 发起方会员编号 `G` + 6 位 |
| `nickname` | string | 是 | 发起方昵称 |
| `user_avatar` | string | 是 | 发起方头像 |
| `target_user_id` | int | 是 | 对象会员 `user_id` |
| `target_member_code` | string | 是 | 对象会员编号 `G` + 6 位 |
| `target_nickname` | string | 是 | 对象昵称 |
| `target_avatar` | string | 是 | 对象头像 |
| `occurred_at` | datetime | 是 | 发生时间（浏览类取该对的 `MAX(created_at)`） |

**`items[].` 浏览类专属**（`category=browse`）：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `browse_times` | int | 同一对会员的浏览次数，页面展示为「第 N 次」 |

**`items[].` 爆灯类专属**（`category=superlike`）：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `amount` | string | 爆灯支付金额（字符串 Decimal） |
| `order_no` | string | 支付订单号 |
| `pay_status` | int | `0` 未支付 / `1` 已支付 |
| `pay_status_label` | string | 未支付 / 已支付 |
| `pay_method` | string | 支付方式 |
| `event_status` | int | 爆灯状态：`1` 生效中 / `2` 已过期 / `3` 已撤销 |
| `event_status_label` | string | 正常 / 已过期 / 已取消 |

**`items[].` 礼物类专属**（`category=gift`）：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `gift_name` | string | 礼物名称 |
| `gift_qty` | int | 礼物数量 |
| `qty_unit` | string | 数量单位：颗 / 发 / 个 / 架 |
| `point_cost` | int | 消耗积分 |
| `paid_amount` | string | 实付金额（字符串 Decimal） |
| `reward_points` | int | 奖励积分 |
| `order_no` | string | 支付订单号 |
| `pay_status` / `pay_status_label` / `pay_method` | int / string / string | 同爆灯类 |

**`items[].` 举报类专属**（`category=report`）：

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `submit_ip` | string | 提交人 IP |
| `report_type` | string | 举报原因 / 类型 |
| `detail` | string | 举报详细内容（源列 `user_report.desc`） |
| `images` | string[] | 证据图片 URL 列表；无图或解析失败为 `null` |
| `report_status` | int | `0` 待处理 / `1` 已处理 / `2` 驳回 |
| `report_status_label` | string | 待处理 / 已处理 / 已驳回 |

#### 返回示例

浏览记录：

```json
{
  "items": [
    {
      "event_id": 10231,
      "user_id": 123,
      "member_code": "G000123",
      "nickname": "小芸",
      "user_avatar": "/storage/avatar/123.webp",
      "target_user_id": 456,
      "target_member_code": "G000456",
      "target_nickname": "阿哲",
      "target_avatar": "/storage/avatar/456.webp",
      "occurred_at": "2026-09-10T08:31:22",
      "browse_times": 5
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

网友举报（含证据图）：

```json
{
  "items": [
    {
      "event_id": 88,
      "user_id": 123,
      "member_code": "G000123",
      "nickname": "小芸",
      "user_avatar": null,
      "target_user_id": 456,
      "target_member_code": "G000456",
      "target_nickname": "阿哲",
      "target_avatar": null,
      "occurred_at": "2026-09-09T12:00:00",
      "submit_ip": "203.0.113.10",
      "report_type": "虚假资料",
      "detail": "照片与本人不符",
      "images": ["/storage/report/88-1.webp"],
      "report_status": 0,
      "report_status_label": "待处理"
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

**空数据示例**（该类别暂无记录）：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "has_more": false
}
```

#### 使用方法与业务规则

1. **前置条件**：当前账号已登录且持有 `matchmaker.member.read` 权限；否则 `401` / `403`。
2. **调用顺序**：进入页面 → 按当前 Tab 传 `category` 调本接口；切换 Tab 重新拉取；点击搜索 / 筛选重置到 `page=1`。
3. **浏览类聚合口径**：按 `(user_id, target_user_id)` 分组，`browse_times = COUNT(*)`，`occurred_at = MAX(created_at)`，`event_id = MIN(id)`；`min_times` 作用在分组后的 `HAVING` 上。
4. **收藏类口径**：固定追加 `f.type = 2`，不区分收藏夹。
5. **排序**：统一 `occurred_at DESC, event_id DESC`。
6. **关键字搜索**：只匹配**发起方**（`users.nickname` / `users.phone` / 编号），不匹配对象方。
7. **分页**：`has_more = page * page_size < total`。
8. **空数据**：`items` 返回空数组，不报错。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 JSON |
|---|---|---|---|
| `401` | 未携带 / Token 过期 | 清除本地 Token 并跳转登录页（`adminApi` 已统一处理） | `{"detail": "Not authenticated"}` |
| `403` | 账号无 `matchmaker.member.read` | 提示无权限，隐藏该菜单 | `{"detail": "缺少权限：matchmaker.member.read"}` |
| `422` | `category` 非枚举 / `page_size` 越界 / `min_times` 越界 | 属前端传参问题，修正后重试 | 见上文非法示例 |

#### 文档完成自检清单

- [x] 基本信息（方法、路径、权限、用途）
- [x] 请求参数表（含位置、类型、必填、默认值、校验、业务含义）
- [x] 请求示例（含 2 个非法示例）
- [x] 返回参数表（分页字段 + 通用字段 + 五类专属字段，均带业务含义）
- [x] 返回示例（含浏览 / 举报两类 + 空数据示例）
- [x] 使用方法与业务规则（前置条件、调用顺序、聚合口径、排序、分页、边界）
- [x] 错误表（状态码 + 触发条件 + 前端处理建议 + 响应 JSON）

---

## 3. 删除行为记录

### 3.1 `DELETE /api/v1/admin/members/behavior-events/{category}/{event_id}`

删除爆灯 / 礼物 / 举报三类记录（**物理删除**）。

#### 基本信息

| 项 | 值 |
|---|---|
| 方法 | `DELETE` |
| 路径 | `/api/v1/admin/members/behavior-events/{category}/{event_id}` |
| 权限 | `matchmaker.member.manage` |
| 用途 | 页面「操作」列的删除按钮（仅爆灯 / 礼物 / 举报三个 Tab 有） |

#### 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `category` | path | string | 是 | — | 正则 `^(superlike\|gift\|report)$` | 记录类别；**浏览 / 收藏不支持删除** |
| `event_id` | path | int | 是 | — | `≥ 1` | 记录主键 |

#### 请求示例

合规：

```http
DELETE /api/v1/admin/members/behavior-events/report/88
Authorization: Bearer <access-token>
```

**非法示例**（对浏览记录发起删除 → `422`）：

```http
DELETE /api/v1/admin/members/behavior-events/browse/10231
```

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["path", "category"],
      "msg": "String should match pattern '^(superlike|gift|report)$'",
      "input": "browse"
    }
  ]
}
```

#### 返回参数

| 字段 | 类型 | 业务含义 |
|---|---|---|
| `id` | int | 被删除的记录主键 |
| `deleted` | bool | 恒为 `true`（删除成功） |

#### 返回示例

```json
{ "id": 88, "deleted": true }
```

#### 使用方法与业务规则

1. **前置条件**：持有 `matchmaker.member.manage`；页面需二次确认后调用（前端已用 `window.confirm`）。
2. **表映射**：`superlike → user_boost`、`gift → user_gift_record`、`report → user_report`。
3. **幂等性**：**非幂等**。记录不存在返回 `404`，重复删除第二次会报 `404`。
4. **审计**：删除成功后写 `business_audit_log`，`action = member.behavior.{category}.delete`，`resource_type` 为被删表名，`resource_id` 为记录 ID。
5. **不可恢复**：物理删除，无软删标记。
6. **前端处理**：成功后从当前列表移除该行并 toast「已删除」。

#### 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 JSON |
|---|---|---|---|
| `401` | 未登录 / Token 过期 | 跳转登录页 | `{"detail": "Not authenticated"}` |
| `403` | 无 `matchmaker.member.manage` | 提示无权限，禁用删除按钮 | `{"detail": "缺少权限：matchmaker.member.manage"}` |
| `404` | `event_id` 对应记录不存在 | 提示「记录不存在」，刷新列表 | `{"detail": "记录不存在"}` |
| `422` | `category` 非 `superlike\|gift\|report`，或 `event_id < 1` | 前端传参错误，修正后重试 | 见上文非法示例 |

#### 文档完成自检清单

- [x] 基本信息（方法、路径、权限、用途）
- [x] 请求参数表（含位置、类型、必填、校验、业务含义）
- [x] 请求示例（含非法示例）
- [x] 返回参数表（每个字段带业务含义）
- [x] 返回示例
- [x] 使用方法与业务规则（前置条件、表映射、幂等性、审计、不可恢复）
- [x] 错误表（状态码 + 触发条件 + 前端处理建议 + 响应 JSON）

---

## 4. 总览：本模块全部端点

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/v1/admin/members/behavior-events` | `matchmaker.member.read` | 五类行为流水分页查询 |
| DELETE | `/api/v1/admin/members/behavior-events/{category}/{event_id}` | `matchmaker.member.manage` | 删除爆灯 / 礼物 / 举报记录 |

## 5. 变更记录

| 版本 | 日期 | 变更前 | 变更后 | 影响范围 |
|---|---|---|---|---|
| v1.0 | 2026-09-11 | 无本文档；前端 `/love-user-behavior` 为硬编码假数据；后端仅有 `GET /admin/members/behavior/all`（四类、字段不全、无调用方） | 新增本文档；新增 `/admin/members/behavior-events`（五类完整字段 + 筛选）与删除端点；前端页面接入真实接口 | 前端 `love-user-behavior/page.tsx`、`admin-endpoints.ts`；后端新增 `schemas/services/routes/member_behavior_admin.py` 并在 `app/api/router.py` 注册；数据库新增 `user_gift_record` 表、`user_boost` 补 `pay_status`/`pay_method`/`status`、`user_report` 补 `submit_ip`（均走 `database_setup_marriage.py` 幂等补列） |
