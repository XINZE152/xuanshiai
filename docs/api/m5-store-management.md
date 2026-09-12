# 分店管理 接口契约（M5）

> 覆盖 3 个后端模块（共 **12 个新增/扩展端点**）：
> 1. **分站/门店**：`app/api/routes/organization_admin.py`（新增 4：列表 / 创建 / 删除 / 报表×2）
> 2. **分店红娘**：复用 `app/api/routes/matchmaker_staff_admin.py`（新增 `in_store` 过滤 + `menu_permission_count`）
> 3. **分店分成明细**：`app/api/routes/finance.py` 内 `admin_router`（新增 4：列表 / 选项 / 统计卡 / 导出）

所有端点均按 `PROJECT_RULES.md` 2.1.1 的统一模板撰写。本文档随代码演进同步维护。

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：**红娘后台独立 Token**（非 C 端 token）
- 请求头：`Authorization: Bearer <access-token>`
- 鉴权函数：`get_current_matchmaker_admin`
- 所有 admin 端点未登录 → **401**；权限点缺失 → **403**

### 1.2 权限点

| 路径前缀 | 读取权限 | 写入权限 |
|---|---|---|
| `/admin/matchmaker/stores/*` | `matchmaker.organization.read` | `matchmaker.organization.manage` |
| `/admin/matchmaker/matchmakers`（`in_store=true`） | `matchmaker.read` | `matchmaker.manage` |
| `/admin/finance/store-commission-*` | `finance.read` | —（只读） |

依赖文件：`app/api/dependencies.py:_matchmaker_admin_permission(request)` 按路径子串自动映射（`/stores` 命中 `matchmaker.organization.*`；`/finance` 命中 `finance.*`）。

### 1.3 响应与异常

- 成功响应**不包裹** `data` 字段
- 错误统一 `HTTPException(status_code, detail="...")` → 响应体 `{"detail": "..."}`，**无业务错误码字段**
- 分页：`items / page / page_size / total / has_more`
- 金额一律用 `Decimal` 字段承载，JSON 序列化为**字符串**（如 `"1234.50"`）
- 时间字段：MySQL `UTC_TIMESTAMP()` 写入，Python 端读取后按 ISO 8601 返回

### 1.4 数据库变更

**新增列（幂等补列，`database_setup_marriage.py:_ensure_m5_columns`）**：

| 表 | 列 | 类型 | 说明 |
|---|---|---|---|
| `organization` | `link_url` | `varchar(255) NULL` | 分站访问链接 |
| `organization` | `sort_order` | `int NOT NULL DEFAULT 0` | 显示排序，数字越大越靠前 |
| `organization` | `qr_code` | `varchar(500) NULL` | 分站链接/二维码图片地址 |

**无新表**。分店红娘复用 `organization_member`（`role_code='store_matchmaker'`）+ `matchmaker_profile`；菜单权限复用已有 `matchmaker_menu_permission` 表。分店分成复用 `commission_entry`（`beneficiary_type='store'`，`beneficiary_id=organization.id`）。

**配置域**：`admin_config_snapshot.tools_branch` 新增 `mode` 键（`all` 全国模式 / `region` 指定地区），默认 `all`。

---

## 二、分站/门店（`/api/v1/admin/matchmaker/stores`）

### 2.1 列表 `GET /admin/matchmaker/stores`

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|---|
| `page` | query | int | 否 | 1 | 1-1000 | 页码 |
| `page_size` | query | int | 否 | 20 | 1-100 | 每页条数 |
| `search` | query | string | 否 | — | ≤64 | 名称/显示名/编码/地区码模糊匹配 |
| `status` | query | int | 否 | — | 1/2/3 | 1正常 2关闭 3停用 |

**返回** `StoreAdminPage`，item 字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int | 主键 |
| `code` | string | 分站编码（唯一，创建后不可改） |
| `name` | string | 分站名称 |
| `display_name` | string \| null | 显示名称 |
| `region_code` | string \| null | 分站地区（行政区划码） |
| `link_url` | string \| null | 分站链接 |
| `sort_order` | int | 显示排序 |
| `qr_code` | string \| null | 链接/二维码图片地址 |
| `status` | 1 \| 2 \| 3 | 状态 |
| `auto_redirect` | bool | 自动跳转 |
| `member_count` | int | 有效成员数（`organization_member.status=1`） |
| `matchmaker_count` | int | 分店红娘数（`role_code IN ('store_manager','store_matchmaker')`） |
| `created_at` / `updated_at` | datetime | 时间戳 |

**空数据示例**：`{"items": [], "page": 1, "page_size": 20, "total": 0, "has_more": false}`

### 2.2 创建 `POST /admin/matchmaker/stores`

**body** `StoreAdminCreate`：

| 字段 | 类型 | 必填 | 校验 | 含义 |
|---|---|---|---|---|
| `code` | string | 是 | `^[A-Za-z0-9_-]{2,64}$` | 唯一编码 |
| `name` | string | 是 | 1-128 | 分站名称 |
| `display_name` | string \| null | 否 | ≤128 | 显示名称 |
| `region_code` | string \| null | 否 | ≤64 | 分站地区 |
| `link_url` | string \| null | 否 | ≤255 | 分站链接 |
| `sort_order` | int | 否 | 0-9999，默认 0 | 显示排序 |
| `qr_code` | string \| null | 否 | ≤500 | 二维码图片地址 |
| `auto_redirect` | bool | 否 | 默认 `false` | 自动跳转 |

**返回**：201 `StoreAdminItem`。

**业务规则**：
- `code` 重复 → **409**「分站编码已存在」（捕获 `IntegrityError`）
- 写审计：`business_audit_log (action='organization.create', resource_type='organization')`

**非法示例**：`{"code": "南京", "name": "x"}` → **422**（code 含中文不匹配 pattern）

### 2.3 详情 `GET /admin/matchmaker/stores/{store_id}`

**path**：`store_id`(≥1)。**返回** `StoreAdminItem`；不存在 → **404**「门店不存在」。

### 2.4 编辑 `PATCH /admin/matchmaker/stores/{store_id}`

**body** `StoreAdminUpdate`：全可选，字段同 2.2（不含 `code`）。提交为空的 body 不报错（仅写审计）。
写审计：`organization.update`。

### 2.5 状态 `PATCH /admin/matchmaker/stores/{store_id}/status`

**body** `{status: 1|2|3, reason?: string(≤255)}`。
写审计：`organization.status`。

### 2.6 删除 `DELETE /admin/matchmaker/stores/{store_id}`

**返回** 被删除的 `StoreAdminItem`。

**业务规则**：
- 仍有有效成员（`member_count > 0`）→ **409**「该分站下仍有成员，请先移除成员后再删除」
- 仍有生效的会员归属（`resource_assignment.status=1`）→ **409**「该分站下仍有生效的会员归属，无法删除」
- 写审计：`organization.delete`

---

## 三、分店红娘（复用 `/api/v1/admin/matchmakers`）

分店红娘与总店红娘共用同一套端点；**分店红娘 = 已挂靠门店**（`organization_member.role_code='store_matchmaker'`）的服务红娘。

### 3.1 列表 `GET /admin/matchmakers`

在原参数（`page/page_size/keyword/store_id/commission_level_id/locked`）基础上**新增**：

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `in_store` | bool \| null | — | `true` 仅分店红娘 / `false` 仅总店红娘 / 不传 全部 |

`MatchmakerStaffItem` 新增字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `menu_permission_count` | int | 已配置的菜单权限条数（`matchmaker_menu_permission`），`0` 表示沿用全部菜单 |

**前端页面用法**：`in_store=true` + 可选 `store_id`。

### 3.2 新增 `POST /admin/matchmakers`

**body** `MatchmakerStaffCreate`，`store_id` 必填（分店红娘）。关键字段映射：

| 页面字段 | 请求字段 |
|---|---|
| 账号绑定（按昵称/按手机） | `lookup` + `lookup_by` |
| 红娘称呼 | `display_name`（同时写入 `users.nickname`） |
| 岗位描述 | `description` |
| 红娘口号 | `slogan` |
| 微信号 / 手机号 | `wechat` / `phone` |
| 红娘角色 | `role_tag`（`super`/`normal`） |
| 修改联系方式 | `contact_editable` |
| 隶属门店 | `store_id` |
| 排序值 | `sort` |
| 头像 / 二维码 | `avatar` / `wechat_qr` |

**业务规则**：账号已绑定红娘 → **409**；`store_id` 存在时写入 `organization_member(role_code='store_matchmaker')`。

### 3.3 编辑 `PUT /admin/matchmakers/{matchmaker_id}`

**body** `MatchmakerStaffUpdate`（全可选）。`store_id` 变更会同步 `organization_member`。

### 3.4 锁定 / 前台展示 / 删除

- `PATCH /admin/matchmakers/{id}/lock` → `{locked: bool}`
- `PATCH /admin/matchmakers/{id}/visibility` → `{visible: bool}`
- `DELETE /admin/matchmakers/{id}` → 软删除（`matchmaker_profile.deleted_at`）

### 3.5 菜单权限

- `GET /admin/matchmakers/{id}/permissions` → `{matchmaker_id, menuIds: number[]}`
- `PUT /admin/matchmakers/{id}/permissions` → `{menuIds: number[]}`
- `GET /admin/menus/tree` → `AdminMenuNode[]`（供勾选树渲染）

---

## 四、分店报表（`/api/v1/admin/matchmaker/stores/{store_id}/report/*`）

### 4.1 统计卡 `GET /admin/matchmaker/stores/{store_id}/report/summary`

**返回** `StoreReportSummary`：

| 字段 | 类型 | 口径 |
|---|---|---|
| `store_id` / `store_name` | int / string | 分店 |
| `lead_count` | int | `customer_lead.organization_id = store` |
| `member_count` | int | `resource_assignment` 中该店生效归属的去重会员数 |
| `online_match_count` | int | 该店成员红娘的 `matchmaker_service.status=2` |
| `online_vip_count` | int | `user_membership` 有效且订单 `product_type <> 'offline_vip'`（按该店归属会员） |
| `offline_vip_count` | int | 同上但 `product_type = 'offline_vip'` |
| `meeting_arranged_count` | int | `meeting_record.organization_id = store` 且非 `CANCELLED` |
| `online_commission` | Decimal→str | `commission_entry(beneficiary_type='store', beneficiary_id=store)` 非 `REVERSED` 汇总 |
| `offline_performance` | Decimal→str | `payment_order.status=1 AND product_type='offline_vip'`（该店归属会员） |
| `meeting_rank` / `online_commission_rank` / `offline_performance_rank` | int | 在全部未停用分店中的排名（1 = 第一） |

> 口径与平台首页 `app/services/admin_home.py:dashboard` 保持一致。

### 4.2 月度报表 `GET /admin/matchmaker/stores/{store_id}/report/monthly`

| 参数 | 类型 | 默认 | 校验 |
|---|---|---|---|
| `months` | int | 6 | 1-24 |

**返回** `StoreReportMonthly {store_id, months: StoreReportMonthlyRow[]}`，按月份升序，缺失月份补零：

| 字段 | 类型 | 含义 |
|---|---|---|
| `month` | string | `YYYY-MM` |
| `new_male_members` / `new_female_members` | int | 当月新增男/女会员（该店归属） |
| `new_leads` | int | 当月新增客源线索 |
| `new_online_vip` / `new_offline_vip` | int | 当月新增线上/线下 VIP 会员 |
| `new_match_requests` | int | 当月新增约见申请 |
| `new_offline_meetings` | int | 当月新增线下约会（非取消） |
| `online_commission` / `offline_performance` | Decimal→str | 当月分店分成 / 线下业绩 |

---

## 五、分店分成明细（`/api/v1/admin/finance/store-commission-*`）

### 5.1 明细列表 `GET /admin/finance/store-commission-entries`

| 参数 | 类型 | 默认 | 校验 | 含义 |
|---|---|---|---|---|
| `page` / `page_size` | int | 1 / 20 | ≥1 / 1-100 | 分页 |
| `store_id` | int \| null | — | ≥1 | 按分店筛选 |
| `matchmaker_id` | int \| null | — | ≥1 | 按订单归属红娘筛选（经 `resource_assignment` 反查） |
| `rule_id` | int \| null | — | ≥1 | 按分成规则（事件）筛选 |
| `start_date` / `end_date` | string \| null | — | `^\d{4}-\d{2}-\d{2}$` | 创建日期范围（`end_date` 闭开：`< end_date + 1 day`） |

**返回** `StoreCommissionEntryPage`，item 字段：`id` `created_at` `store_id` `store_name` `matchmaker_id` `matchmaker_name` `consumer_id` `consumer_name` `event_name` `order_id` `order_no` `consumer_amount`(str) `commission_amount`(str) `status`。

**非法示例**：`start_date=2026/09/01` → **422**（不匹配 pattern）。

### 5.2 筛选项 `GET /admin/finance/store-commission-entries/options`

**返回** `StoreCommissionOptions {stores, matchmakers, events}`：
- `stores`：全部分店（`Organization`，含 `status`）
- `matchmakers`：产生过分店分成的红娘（去重）
- `events`：启用的 `commission_rule`

### 5.3 统计卡 `GET /admin/finance/store-commission-summary`

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `store_id` | int \| null | — | 不传则统计全部分店 |

**返回** `StoreCommissionSummary`：

| 字段 | 口径 |
|---|---|
| `total_amount` | 累计分得（非 `REVERSED`） |
| `current_month_amount` | 本月（`UTC` 当月 1 日起） |
| `previous_month_amount` | 上月整月 |
| `pending_amount` | `status='PENDING'` |

### 5.4 导出 `GET /admin/finance/store-commission-entries/export`

参数同 5.1（**无分页**，导出当前筛选全量），返回 `.xlsx`（`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`），表头：
`ID / 时间 / 分店名称 / 红娘 / 消费会员 / 分成/奖励事件 / 消费金额 / 分成金额 / 状态`。

前端调用：`adminEndpoints.exportStoreCommissionEntries(query)`（内部走 `downloadAdminFile(path, filename, query)`）。

---

## 六、错误码表

| HTTP | 触发条件 | 前端处理 |
|---|---|---|
| 401 | 未登录 / Token 失效 | 跳转登录页 |
| 403 | 权限点缺失 | 提示无权限 |
| 404 | `store_id` 不存在 / 红娘不存在 | 提示并刷新列表 |
| 409 | 编码重复 / 分站有成员或有生效归属时删除 / 账号已绑定红娘 | 弹出 `detail` 文案 |
| 422 | 参数校验失败（编码格式、日期格式、排序越界） | 表单内联提示 |

---

## 七、前端对应页面

| 页面 | 路径 | 使用端点 |
|---|---|---|
| 分站配置 | `/branch-config` | `GET/POST/PATCH/DELETE /stores`、配置域 `tools_branch` |
| 门店管理 | `/mendian-list` | 无（UI 本身为「需升级旗舰版」占位说明，保持原样） |
| 分店红娘 | `/branch-matchmaker-list` | `GET /matchmakers?in_store=true`、`POST/PUT /matchmakers`、lock/visibility/permissions、`GET /dict/stores` |
| 分店报表 | `/branch-report-list` | `GET /stores`（切换）、`/stores/{id}/report/summary`、`/stores/{id}/report/monthly` |
| 分成明细 | `/branch-distribution-list` | `/finance/store-commission-entries`（+options+summary+export） |

---

## 八、文档完成自检清单

- [x] 每个端点的请求参数表（参数名/位置/类型/必填/默认值/校验/业务含义）
- [x] 至少 1 个非法示例（`code` 含中文 422、日期格式 422）
- [x] 每个返回体的字段表（含业务口径说明）
- [x] 空数据示例（`items: []`）
- [x] 业务规则（唯一约束、删除前置校验、金额口径、排名规则）
- [x] 错误码表（HTTP + 触发条件 + 前端处理）
- [x] 与代码（`app/api/routes/*.py`、`app/services/*.py`、`app/schemas/*.py`）一致

## 九、变更记录

| 日期 | 版本 | 变更 | 影响 |
|---|---|---|---|
| 2026-09-12 | v1 | 新建：分站/门店 4 + 分店报表 2 + 分店分成明细 4 = 12 端点；`organization` 补 3 列；`tools_branch` 增 `mode` | M5 后端首版契约 |
