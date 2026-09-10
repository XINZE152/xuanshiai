# 推广红娘 → 分成配置 (4 固定级别) 后台 API

> **归属页面**：推广红娘 → 分成配置  
> **URL**：前端 `xuanshiai.com/admin/poplove-matchmaker-distribution`  
> **页面结构**：4 行总览表（ID/分成级别/级别名称/分成模式/红娘数量/自动升级条件/操作）+ 行内「编辑配置」弹窗  
> **后端统一前缀**：`/api/v1/admin/promoter-levels`  
> **权限点**：`matchmaker.read`（读）/ `matchmaker.manage`（写）  
> **数据表**：`promoter_level_config`（uk `level_id`，4 行种子由 `database_setup_marriage.py` 幂等写入）

---

## 一、通用约定

- **登录要求**：所有端点都需要红娘后台登录态（`Authorization: Bearer <access_token>`），未登录返回 `401`。
- **4 固定级别**：`level_id ∈ {1, 2, 3, 4}` 对应 1 初级 / 2 推广大师 / 3 推广大使 / 4 推广天使，**不开放后台新增/删除/重排**。
- **分成模式**：`auto_split_mode` 字段硬编码：级别 1/3/4 = `fixed_amount`（列表显示「自定义固定金额」）；级别 2 = `auto_rate` rate=10（列表显示「按同比自动计算：10%」）。弹窗不直接编辑该字段。
- **编辑范围**（`PUT`）：仅允许修改 4 个业务参数——`promote_threshold` / `register_reward_male` / `register_reward_female` / `consume_commission_mode` / `consume_commission_rate`。
- **金额序列化**：`register_reward_*` / `consume_commission_rate` / `auto_split_rate` 一律以字符串返回（如 `"5.00"`、`"10.0000"`），前端需 `Number()` 解析展示。
- **红娘数量**：聚合 `user_matchmaker_apply(application_type='promoter', status=1, commission_level_id=level_id)` 计数。

---

## 二、接口清单

| # | Method | Path | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/api/v1/admin/promoter-levels` | 取 4 行分成配置（页面加载用） | matchmaker.read |
| 2 | GET | `/api/v1/admin/promoter-levels/{level_id}` | 取单个分成级别 | matchmaker.read |
| 3 | PUT | `/api/v1/admin/promoter-levels/{level_id}` | 编辑分成级别（业务参数） | matchmaker.manage |

---

## 三、详细契约

### 3.1 `GET /api/v1/admin/promoter-levels`

**基本信息**
- 用途：返回 4 行分成配置，按 `level_id` 升序。
- 权限：`matchmaker.read`；成功 `200`。

**响应 200** — `PromoterLevelPage`

```json
{
  "items": [
    {
      "id": 5,
      "level_id": 1,
      "level_name": "初级",
      "auto_split_mode": "fixed_amount",
      "auto_split_mode_label": "自定义固定金额",
      "auto_split_rate": null,
      "promote_threshold": null,
      "promote_threshold_text": "默认",
      "matchmaker_count": 7,
      "register_reward_male": "0.00",
      "register_reward_female": "0.00",
      "consume_commission_mode": "none",
      "consume_commission_rate": null,
      "updated_at": "2026-09-08T10:00:00"
    },
    {
      "id": 6,
      "level_id": 2,
      "level_name": "推广大师",
      "auto_split_mode": "auto_rate",
      "auto_split_mode_label": "按同比自动计算：10%",
      "auto_split_rate": "10.0000",
      "promote_threshold": 51,
      "promote_threshold_text": "累计发展有效相亲会员数量>=51人",
      "matchmaker_count": 0,
      "register_reward_male": "0.00",
      "register_reward_female": "0.00",
      "consume_commission_mode": "auto_rate",
      "consume_commission_rate": "10.0000",
      "updated_at": "2026-09-08T10:00:00"
    }
  ]
}
```

**字段说明**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 主键 |
| `level_id` | 1/2/3/4 | 业务级别 ID |
| `level_name` | string | 级别名称（初级/推广大师/推广大使/推广天使） |
| `auto_split_mode` | enum | 硬编码模式：fixed_amount / auto_rate |
| `auto_split_mode_label` | string | 列表「分成模式」列展示文案（自动派生） |
| `auto_split_rate` | string(decimal)? | 同比比例(%)，auto_rate 时为 10.0000 |
| `promote_threshold` | int? | 累计发展有效相亲会员数阈值，null/0 = 默认/不限制 |
| `promote_threshold_text` | string | 列表「自动升级条件」列展示文案（自动派生） |
| `matchmaker_count` | int | 当前处于该级别的在职红娘数 |
| `register_reward_male` | string(decimal) | 男会员注册奖励(元/人) |
| `register_reward_female` | string(decimal) | 女会员注册奖励(元/人) |
| `consume_commission_mode` | enum | none / auto_rate |
| `consume_commission_rate` | string(decimal)? | 会员消费分成比例(%)，auto_rate 时必填 |
| `updated_at` | datetime | 最后修改时间 |

---

### 3.2 `GET /api/v1/admin/promoter-levels/{level_id}`

- 用途：单个分成级别详情。  
- 路径参数：`level_id` 1-4（非法 → 422）。  
- 权限：`matchmaker.read`；成功 `200`。

**响应 200**：同 3.1 单条。  
**响应 404**：`{"detail": "分成级别不存在"}`

---

### 3.3 `PUT /api/v1/admin/promoter-levels/{level_id}`

- 用途：编辑 1 个分成级别的业务参数。未传字段保留原值。  
- 路径参数：`level_id` 1-4。  
- 权限：`matchmaker.manage`；成功 `200`。

**请求体** — `PromoterLevelUpdate`（所有字段可选）

```json
{
  "promote_threshold": 51,
  "register_reward_male": "0.00",
  "register_reward_female": "0.00",
  "consume_commission_mode": "auto_rate",
  "consume_commission_rate": "10.0000"
}
```

**业务约束**

| 场景 | 规则 |
|---|---|
| `promote_threshold` | 0 / null → 存为 null（不限制） |
| `register_reward_male/female` | ≥ 0，上限 1,000,000 元，2 位小数 |
| `consume_commission_mode=auto_rate` | 必须同时填 `consume_commission_rate`（若当前行无 rate 则 400） |
| `consume_commission_mode=none` | 自动清空 `consume_commission_rate` |
| `consume_commission_rate` | 0~100% |

**响应 200**：`PromoterLevelItem`（字段同 3.1）。  
**响应 400**：`{"detail": "会员消费分成为按比例时必须填写比例"}`  
**响应 404**：`{"detail": "分成级别不存在"}`  
**响应 422**：Pydantic 校验失败（threshold 负数、rate 超 100、mode 非法等）。

---

## 四、迁移与建表

1. **新建 / 迁移老库**：执行 `python database_setup_marriage.py`，脚本会幂等：
   - `CREATE TABLE IF NOT EXISTS promoter_level_config (...)`
   - `INSERT IGNORE INTO promoter_level_config` 写入 4 行种子（与截图一致：级 1/3/4 auto_split_mode=fixed_amount，级 2 auto_split_mode=auto_rate rate=10%）
2. **运营调整**：通过「编辑配置」按钮触发 PUT，编辑业务参数；auto_split_mode 不允许变更。
3. **关联表**：`user_matchmaker_apply.commission_level_id` 是本表的外键引用（应用层），但 schema 上没有 FK 约束（commission_level_id 是 1-4 整数枚举）。

---

## 五、测试与验证

单测：`tests/test_promoter_level_admin.py`（OpenAPI 注册 3 端点 / 未登录 401 / promote_threshold 下界 / register_reward 上限 / consume_commission_rate 上限 100）。  
执行：`uv run pytest tests/test_promoter_level_admin.py -q` → 5 passed。  
全量回归：`uv run pytest tests/test_promoter_*.py tests/test_commission_*.py tests/test_apportion_*.py -q` → 39 passed。
