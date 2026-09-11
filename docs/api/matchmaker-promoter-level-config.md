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
- Method / URL：`GET /api/v1/admin/promoter-levels`（无请求体、无 query）。
- 权限：`matchmaker.read`；需要登录；成功 `200`。
- Content-Type：`application/json`；响应 Content-Type：`application/json`。

**请求示例**

```http
GET /api/v1/admin/promoter-levels
Authorization: Bearer <access_token>
```

无请求体。非法示例：本接口无入参，无非法示例。

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

**字段说明（返回参数表）**

| 字段 | 类型 | 必返 | 业务含义 | 示例值 |
|---|---|---|---|---|
| `id` | int | 是 | 主键 | `5` |
| `level_id` | 1/2/3/4 | 是 | 业务级别 ID | `1` |
| `level_name` | string | 是 | 级别名称（初级/推广大师/推广大使/推广天使） | `"初级"` |
| `auto_split_mode` | enum | 是 | 硬编码模式：fixed_amount / auto_rate（不可编辑） | `"fixed_amount"` |
| `auto_split_mode_label` | string | 是 | 列表「分成模式」列展示文案（自动派生） | `"自定义固定金额"` |
| `auto_split_rate` | string(decimal)? | 是 | 同比比例(%)，auto_rate 时为 `"10.0000"`；否则 `null` | `null` |
| `promote_threshold` | int? | 是 | 累计发展有效相亲会员数阈值，null/0 = 默认/不限制 | `null` |
| `promote_threshold_text` | string | 是 | 列表「自动升级条件」列展示文案（自动派生） | `"默认"` |
| `matchmaker_count` | int | 是 | 当前处于该级别的在职红娘数（聚合 `user_matchmaker_apply`） | `7` |
| `register_reward_male` | string(decimal) | 是 | 男会员注册奖励(元/人)，字符串 | `"0.00"` |
| `register_reward_female` | string(decimal) | 是 | 女会员注册奖励(元/人)，字符串 | `"0.00"` |
| `consume_commission_mode` | enum | 是 | none / auto_rate | `"none"` |
| `consume_commission_rate` | string(decimal)? | 是 | 会员消费分成比例(%)，auto_rate 时必填；否则 `null` | `null` |
| `updated_at` | datetime | 是 | 最后修改时间 | `"2026-09-08T10:00:00"` |

> 返回为对象数组（非分页），固定 4 行；无「空数据」场景（至少返回 `[]`）。

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 页面加载即调用，按 `level_id` 升序渲染 4 行总览表。
- `auto_split_mode` / `auto_split_rate` 由种子数据硬编码，后台不可改；仅 5 个业务参数可经 `PUT` 调整（见 3.3）。
- `matchmaker_count` 为实时聚合，改动红娘 `commission_level_id` 后下次刷新即变。
- 金额字段均为字符串，前端 `Number()` 解析展示。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义（无入参，已标注）
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例（非分页，已说明）
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、调用顺序、边界场景
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例（无入参，N/A）

---

### 3.2 `GET /api/v1/admin/promoter-levels/{level_id}`

**基本信息**
- 用途：单个分成级别详情。
- Method / URL：`GET /api/v1/admin/promoter-levels/{level_id}`（无请求体）。
- 权限：`matchmaker.read`；需要登录；成功 `200`。
- Content-Type：`application/json`；响应 Content-Type：`application/json`。

**请求参数（path）**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验与含义 |
| --- | --- | --- | --- | --- | --- |
| `level_id` | path | int | 是 | — | 业务级别 ID，取值 1/2/3/4；非法（非 1-4 或非整数）→ `422` |

**请求示例**

```http
GET /api/v1/admin/promoter-levels/2
Authorization: Bearer <access_token>
```

无请求体。非法示例：`GET /api/v1/admin/promoter-levels/5` → `422`（超出 1-4）。

**响应 200**：同 3.1 单条（`PromoterLevelItem`）。
**响应 404**：`{"detail": "分成级别不存在"}`

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.read`。
- 用于「编辑配置」弹窗打开时回填当前级别参数；`level_id` 仅 4 个固定值。
- 与 3.1 共用返回结构，字段业务含义见 3.1 返回参数表。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.read | `{"detail":"无权访问"}` | 提示无权限 |
| 404 | `level_id` 在库无对应行 | `{"detail":"分成级别不存在"}` | 提示级别不存在 |
| 422 | `level_id` 非 1-4 / 非整数 | `{"detail":[...]}` | 按提示修正 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（无请求体时已明确标注）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、调用场景
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例

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

**请求示例（完整）**

```http
PUT /api/v1/admin/promoter-levels/2
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "promote_threshold": 51,
  "register_reward_male": "0.00",
  "register_reward_female": "0.00",
  "consume_commission_mode": "auto_rate",
  "consume_commission_rate": "10.0000"
}
```

非法示例：

```json
{ "consume_commission_mode": "auto_rate", "consume_commission_rate": "120.0000" }
```

→ `422`（`consume_commission_rate` 超出 0~100%）。

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

**使用方法与业务规则**
- 前置条件：登录态 + `matchmaker.manage`。
- 仅校验通过的管理员可改；未传字段保留原值（null 视为未修改）。
- `auto_split_mode` / `auto_split_rate` 不在可编辑范围，传入被忽略。
- 写入后 `updated_at` 刷新；前端刷新 3.1 列表即可见新值。
- 幂等：重复提交相同参数结果一致。

**错误**

| HTTP | 触发 | 响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `auto_rate` 模式但未填 rate | `{"detail":"会员消费分成为按比例时必须填写比例"}` | 提示补全比例 |
| 401 | 未登录/令牌失效 | `{"detail":"请先登录红娘后台"}` | 跳转登录 |
| 403 | 无 matchmaker.manage | `{"detail":"无权访问"}` | 提示无权限 |
| 404 | `level_id` 不存在 | `{"detail":"分成级别不存在"}` | 提示级别不存在 |
| 422 | 校验失败（threshold 负/rate>100/mode 非法/金额格式） | `{"detail":[...]}` | 按提示修正 |

**文档完成自检清单**
- [ ] 有请求参数表，且每个参数都写了业务含义
- [ ] 有完整请求体示例（含非法示例）
- [ ] 有返回参数表，每个字段都写了业务含义，嵌套结构已展开
- [ ] 有成功返回示例
- [ ] 有"使用方法与业务规则"小节，覆盖前置条件、编辑范围、幂等
- [ ] 有错误码表：HTTP 状态码、触发条件、前端处理建议
- [ ] 至少一个非法参数示例

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

---

## 六、变更记录

### 2026-09-11 文档自检补全（代码无改动）

**变更前**
- 3.1/3.2/3.3 仅含基础「基本信息 + 响应示例 + 字段说明/业务约束」片段，缺统一模板要求的：请求示例（完整 URL/Headers）、请求参数表、返回参数表缺「必返/示例值」列、「使用方法与业务规则」独立小节、统一错误码表、文档完成自检清单。

**变更后**
- 按 `PROJECT_RULES.md` 2.1.1 模板补齐：
  - 3.1：补充 Method/URL/Content-Type、请求示例、返回参数表加「必返/业务含义/示例值」列、新增「使用方法与业务规则」「错误」小节与自检清单。
  - 3.2：补充请求参数表（path）、请求示例、非法示例（`level_id=5`→422）、「使用方法与业务规则」「错误」表与自检清单。
  - 3.3：补充完整请求示例（含非法示例 rate>100→422）、「使用方法与业务规则」「错误」表与自检清单。
- 接口路径、权限、字段名、业务规则、错误文案均**未改变**（模块代码本次未改动）。

**影响范围**
- 纯文档补全，不影响任何运行时代码或前端契约；字段名/枚举/错误文案与既有实现一致。
