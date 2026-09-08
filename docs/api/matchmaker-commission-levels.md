# 总店红娘 → 分成配置 后台 API

> **归属页面**：总店红娘 → 红娘管理 → 分成配置  
> **URL**：前端 `xuanshiai.com/admin/love-matchmaker-distribution`  
> **页面结构**：固定 4 行总览表（**初级分成 / 中级分成 / 高级分成 / 合伙分成**）+ 行内「编辑配置」弹窗  
> **后端统一前缀**：`/api/v1/admin/commission-levels`  
> **权限点**：`commission.read`（读）/ `commission.write`（写）  
> **数据表**：`commission_level`（uk: `code`，4 行种子由 `database_setup_marriage.py` 幂等写入）

---

## 一、通用约定

- **登录要求**：所有端点都需要红娘后台登录态（`Authorization: Bearer <access_token>`），未登录返回 `401`。
- **响应包络**：本组接口直接返回 `data`（沿用 `reward_rule_admin` / `apportion_config_admin` 三段式风格）。
- **数据来源**：
  - 列表 / 详情查询：`commission_level` LEFT JOIN `matchmaker_profile` 统计当前适用红娘（仅统计 `deleted_at IS NULL AND locked = 0` 的档案）。
- **审计**：所有 `PUT` 会写入 `business_audit_log`，`resource_type='commission_level'`，`action='commission_level.update'`，`actor_user_id` 来自红娘后台会话，`after_json` 字段保存更新字段集合。
- **种子锁定**：4 行种子的 `code` 字段由建表脚本幂等锁定，**不支持修改**；运营调整通过更新 `name / mode / 数值字段 / promotion_condition / sort / status` 完成。
- **Decimal 序列化**：所有数值字段以字符串形式返回（如 `"10.0000"`、`"5.00"`），前端需用 `Number()` / `parseFloat()` 解析，避免精度丢失。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/commission-levels` | 取 4 行分成级别（页面加载用） | commission.read |
| 2 | GET | `/api/v1/admin/commission-levels/{level_id}` | 取单个分成级别 | commission.read |
| 3 | PUT | `/api/v1/admin/commission-levels/{level_id}` | 编辑分成级别（除 code 外的全部业务字段） | commission.write |

---

## 三、详细契约

### 3.1 `GET /api/v1/admin/commission-levels`

**基本信息**
- 用途：返回 4 行分成级别（按 `sort, id` 升序），含每级的「当前适用红娘」统计。
- 权限：`commission.read`。

**响应 200** — `List[CommissionLevel]`

```json
[
  {
    "id": 1,
    "code": "junior",
    "name": "初级分成",
    "mode": "rate",
    "rate_percent": "10.0000",
    "fixed_amount": null,
    "platform_extra_amount": "5.00",
    "promotion_condition": "默认",
    "sort": 1,
    "status": 1,
    "applicable_matchmaker_count": 12,
    "updated_by": null,
    "created_at": "2026-09-08T10:00:00",
    "updated_at": "2026-09-08T10:00:00"
  },
  {
    "id": 4,
    "code": "partner",
    "name": "合伙分成",
    "mode": "rate",
    "rate_percent": "25.0000",
    "fixed_amount": null,
    "platform_extra_amount": "5000.00",
    "promotion_condition": "牵线成功累计>=300次",
    "sort": 4,
    "status": 1,
    "applicable_matchmaker_count": 3,
    "updated_by": 1,
    "created_at": "2026-09-08T10:00:00",
    "updated_at": "2026-09-08T16:00:00"
  }
]
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 主键 |
| `code` | string | 业务编码（`junior` / `intermediate` / `senior` / `partner`），种子数据锁定 |
| `name` | string | 显示名（运营可改） |
| `mode` | string | 分成模式：`rate`（按订单比例）/ `fixed`（按订单固定金额） |
| `rate_percent` | string(decimal) | 分成比例（%），`mode=rate` 时生效；`mode=fixed` 时后端会强制写 `0` |
| `fixed_amount` | string(decimal)? | 固定分成金额（元），`mode=fixed` 时必填；`mode=rate` 时为 `null` |
| `platform_extra_amount` | string(decimal) | 平台额外奖励（元），每达成一次分成订单额外发放 |
| `promotion_condition` | string? | 自动升级到本级别的条件描述（自由文本，前端展示用） |
| `sort` | int | 排序值，越小越靠前 |
| `status` | int | 1 启用 / 2 停用 |
| `applicable_matchmaker_count` | int | `matchmaker_profile.commission_level_id` 引用此级别的**有效**红娘数 |
| `updated_by` | int? | 最后修改人（红娘后台账号 ID） |
| `created_at` / `updated_at` | datetime | MySQL 时间戳 |

---

### 3.2 `GET /api/v1/admin/commission-levels/{level_id}`

**基本信息**
- 用途：取单个分成级别详情。  
- 路径参数：`level_id` ≥ 1。  
- 权限：`commission.read`。

**响应 200** — `CommissionLevel`（字段同 3.1）。

**响应 404**：`{"detail": "分成级别不存在"}`

---

### 3.3 `PUT /api/v1/admin/commission-levels/{level_id}`

**基本信息**
- 用途：编辑单个分成级别。除 `code` 外所有业务字段均可改，未传字段保留原值。  
- 路径参数：`level_id` ≥ 1。  
- 权限：`commission.write`。

**请求体** — `CommissionLevelUpdate`（所有字段可选，至少传一个）

```json
{
  "name": "中级分成",
  "mode": "rate",
  "rate_percent": "15.0000",
  "fixed_amount": null,
  "platform_extra_amount": "1000.00",
  "promotion_condition": "牵线成功累计>=10次",
  "sort": 2,
  "status": 1
}
```

**业务约束**

| 场景 | 规则 |
|---|---|
| `mode=rate` | 必填 `rate_percent`；后端会自动把 `fixed_amount` 置 `null` |
| `mode=fixed` | 必填 `fixed_amount`；后端会自动把 `rate_percent` 置 `0` |
| `mode=fixed` 但 `fixed_amount` 为空 | Pydantic `model_validator` 抛 `422` |
| `rate_percent` 范围 | `[0, 100]` |
| `fixed_amount` / `platform_extra_amount` | `≥ 0`，上限 10,000,000 元 |
| `name` 长度 | `1 ~ 64` |
| `promotion_condition` 长度 | `≤ 255` |
| `status` | 仅 `1` 或 `2` |

**响应 200** — `CommissionLevel`（字段同 3.1）。

**响应 400**：`{"detail": "mode=fixed 时必须填写 fixed_amount"}`  
**响应 404**：`{"detail": "分成级别不存在"}`  
**响应 422**：Pydantic 校验失败。

---

## 四、迁移与建表

1. **新建 / 迁移老库**：执行 `python database_setup_marriage.py`，脚本会幂等：
   - `CREATE TABLE IF NOT EXISTS commission_level (...)`
   - `ALTER TABLE commission_level ADD COLUMN mode / fixed_amount / platform_extra_amount / promotion_condition`（如列已存在则跳过）
   - `INSERT IGNORE INTO commission_level` 写入 4 行种子（`junior` / `intermediate` / `senior` / `partner`）
2. **运营切换策略**：种子 `code` 不可改；运营调整只能走「编辑配置」按钮触发 PUT。
3. **关联表**：`matchmaker_profile.commission_level_id` 是本表的外键引用，更改 `code` 会破坏关联 — 这是 seed 锁定 `code` 的根本原因。