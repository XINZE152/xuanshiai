# 客源智能分派（auto_assign）接口

> 2026-10-28 新增。用途：录入客源时（或事后对既有客源）按平台「分派配置」自动归属服务红娘。
> 策略计算**全部在服务端**完成（轮询池、指定红娘有效性等都是后端数据，前端算不出公平分派）。
>
> 本期落地策略：`designated`（指定红娘）、`round_robin_random`（在"前台展示且非锁定"的服务红娘池里**纯随机**取一人，无状态）。
> 其余 `by_region / by_promoter / by_partner / none`、未配置、配置停用、池为空——一律视为"选不到人"，
> 客源**保持未分派**（`matchmaker_id=null`）并返回 `assigned=false`，前端据此提示。
>
> 为什么没有 by_region/by_partner：`customer_lead` 表无省/市/现居地字段、无合伙人字段，缺数据源；
> `by_promoter` 的推广人未必是有效服务红娘，写入 `matchmaker_id` 会违反归属校验。三档本期均走"未分派"，
> 与 `apportion_config_admin.upsert_assign` 对 customer_lead 拒绝这些 strategy 的既有口径一致。真·轮询需持久游标，本期以纯随机替代。

## 通用约定

前缀 `/api/v1`，需 `Authorization: Bearer <access-token>`，账号须具有 `customer_lead.manage`（或 `*`）权限，
与既有客源接口同域同权限；受 D-16 数据范围（SELF/STORE/ORGANIZATION/ALL）约束。请求/响应均为 `application/json`。

分派策略来自 `matchmaker_apportion_config` 中 `scope='customer_lead' AND config_type='assign'` 且 `is_enabled=1` 的行
（读写入口见「分派配置」后台，权限 `matchmaker.apportion.write`）。

---

## 1. 录入客源时自动分派（既有接口增字段）

**`POST /admin/customer-leads`**（录入客源线索）——请求体新增 `auto_assign`，响应体新增 `assigned`。

**请求新增参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|---|
| `auto_assign` | body | bool | 否 | `false` | — | `true`=创建即按策略分派；`false`=待分派（保持既有行为） |

其余请求字段与既有 `CustomerLeadCreate` 完全一致，未改动。

**响应变化**

成功状态码由 `201` 返回 `CustomerLeadCreateResult`：即原 `CustomerLead` 全部字段 **+** `assigned`。

| 字段 | 类型 | 必返 | 含义 |
|---|---|---|---|
| `assigned` | bool | 是 | 本次是否成功写入归属红娘。`true` 时 `matchmaker_id` 非空；`false` 表示按策略未选到人，`matchmaker_id` 保持为空 |

> 该响应类型仅用于建单/智能分派两个端点；列表与详情仍返回精简 `CustomerLead`，不会都多出 `assigned`。

**分派时机**：`auto_assign=true` 时，服务端在**同一建单事务内**选好红娘并写入 `matchmaker_id`，不产生"先建后改"的中间态。

**示例**

```
POST /api/v1/admin/customer-leads
Authorization: Bearer <access-token>
Content-Type: application/json

{ "name": "小李", "phone": "13800138000", "source": "抖音", "intention_level": 6, "gender": 1, "auto_assign": true }
```

```json
// 选到人
{ "id": 1024, "name": "小李", "...": "（原 CustomerLead 字段）", "matchmaker_id": 55, "assigned": true }

// 选不到人（未配置/停用/none/池空）
{ "id": 1025, "name": "小李", "...": "（原 CustomerLead 字段）", "matchmaker_id": null, "assigned": false }
```

---

## 2. 对既有客源按策略重算分派

**基本信息**

| 项 | 值 |
|---|---|
| 用途 | 对已存在的单条客源按分派配置重算归属红娘 |
| URL | `/api/v1/admin/customer-leads/{lead_id}/auto-assign` |
| Method | `POST` |
| 需要登录 | 是 |
| 所需权限 | `customer_lead.manage`（或 `*`） |
| 请求 Content-Type | `application/json` |
| 成功状态码 | `200`（返回 `CustomerLeadCreateResult`） |

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 含义 |
|---|---|---|---|---|---|
| `lead_id` | path | int | 是 | 无 | 客源 ID（`ge=1`） |
| `force` | body | bool | 否 | `false` | `false`=仅对当前**未分派**的客源生效；`true`=即使已有归属也按策略**重算并覆盖** |

- 请求体可为空对象 `{}`（等价 `force=false`）。

**返回参数**：同建单响应的 `CustomerLeadCreateResult`（含 `assigned`）。

**业务规则**

- `lead_id` 不在当前账号数据范围或不存在 → `404`。
- 未 `force` 且已有 `matchmaker_id` → **不改库**，直接返回 `assigned=true`（沿用现有归属）。
- 选不到人（策略不可用/池空/未配置/停用）→ **不改库**，返回 `assigned=false`；既有归属不受影响。
- 选到人（含 `force` 覆盖）→ 写入 `matchmaker_id` 并记 `business_audit_log`（action=`customer_lead.auto_assign`），返回 `assigned=true`。
- 幂等：本端点是有状态写操作，重复调用在"已分派且非 force"时为无副作用；如需防抖可传 `Idempotency-Key`（非强制）。

**示例**

```
POST /api/v1/admin/customer-leads/1024/auto-assign
Authorization: Bearer <access-token>
Content-Type: application/json

{ "force": false }
```

**错误**

| HTTP | 触发条件 | 前端处理建议 |
|---|---|---|
| `401` | 未登录 | 跳登录 |
| `403` | 无 `customer_lead.manage` 权限 | 提示无权限 |
| `404` | 客源不存在或不在数据范围 | 刷新列表 |
| `422` | 路径/请求体校验失败 | 检查 lead_id/force 类型 |

---

## 兼容性与部署说明

- **纯新增语义**：`auto_assign` 默认 `false`，`CustomerLeadCreate` 既有必填/校验不变；老前端不传即维持"待分派"，向后兼容。
- **无迁移**：不改表结构；读取既有 `matchmaker_apportion_config` 表。上线仅需拉代码+重启。
- **前置数据**：要让 `auto_assign=true` 真正分派人，需在「分派配置」后台为 `customer_lead/assign` 设 `is_enabled=1` 且 `strategy` 为 `designated`（并指定有效服务红娘）或 `round_robin_random`（且池中存在 `visible=1 且 locked=0` 的服务红娘）。否则 `assigned=false`。
- 相关文件：`app/schemas/customer_lead_admin.py`（`CustomerLeadCreate.auto_assign` / `CustomerLeadCreateResult.assigned` / `LeadAutoAssignRequest`）、`app/services/lead_apportion.py`（策略引擎）、`app/services/customer_lead_admin.py`（`create_lead` 接入 + `auto_assign_lead`）、`app/api/routes/customer_leads_admin.py`（建单响应 + `POST /{lead_id}/auto-assign`）。
