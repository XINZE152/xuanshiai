# AI 画像指标历史接口

> **2026-11-09 实现口径**：按版本实时聚合现有 `ai_profile_revision` 与 `ai_profile_revision_field` 两张表，零新增表、零新增列、零迁移；无字段的旧版本留空，不补零。本文其余「快照」表述均指该版本的冻结字段集合。

## 基本信息

- **路径**: `GET /api/v1/ai/profile-metrics/history`
- **用途**: 查询用户画像指标的历史快照数据，支持前端曲线图展示
- **需要登录**: 是
- **所需权限**: AI 画像功能已开放（`AI_FEATURE_PROFILE`）
- **Content-Type**: application/json
- **成功状态码**: 200

## 请求参数

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 含义 |
|--------|------|------|------|--------|----------|------|
| subject | query | string | 是 | - | `personal` 或 `ideal_partner` | 画像主体：personal=自己，ideal_partner=期待对象 |
| limit | query | integer | 否 | 20 | 1-100 | 返回快照点数上限 |

**合法示例**:
```
GET /api/v1/ai/profile-metrics/history?subject=personal&limit=20
```

**非法示例**:
- `subject=other` → 422，subject 必须为 personal 或 ideal_partner
- `limit=0` → 422，limit 必须 ≥1
- `limit=200` → 422，limit 不得超过 100

## 请求示例

```bash
curl -X GET "https://xhztest.xyz/api/v1/ai/profile-metrics/history?subject=personal&limit=20" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 返回参数

| 字段名 | 类型 | 必返 | 含义 | 示例值 |
|--------|------|------|------|--------|
| subject | string | 是 | 画像主体 | "personal" |
| points | array | 是 | 历史快照点列表，按 published_at 升序 | 见下方 |
| has_more | boolean | 是 | 是否还有更多点（用于分页提示） | false |
| notice | string | 是 | 口径说明与免责 | "理解覆盖度仅供参考..." |

**points[] 元素结构**:

| 字段名 | 类型 | 必返 | 含义 |
|--------|------|------|------|
| revision_id | integer | 是 | 版本 ID |
| revision_no | integer | 是 | 版本号 |
| published_at | string\|null | 是 | 发布时间（ISO 8601），null 表示未发布 |
| overall_percent | float\|null | 是 | 六维平均覆盖度，0-100，null 表示无数据 |
| confirmed_field_count | integer\|null | 是 | 已确认结构化字段数，null 表示无数据 |
| entry_count | integer\|null | 是 | 条目总数，null 表示无数据 |
| dimensions | array | 是 | 六维详细指标 | 见下方 |

**dimensions[] 元素结构**:

| 字段名 | 类型 | 必返 | 含义 |
|--------|------|------|------|
| key | string | 是 | 维度键（六维之一） |
| percent | float | 是 | 该维度覆盖度，0-100 |
| evidence_count | integer | 是 | 该维度证据数 |

## 返回示例

**成功响应（有数据）**:
```json
{
  "subject": "personal",
  "points": [
    {
      "revision_id": 101,
      "revision_no": 1,
      "published_at": "2026-10-15T08:30:00Z",
      "overall_percent": 33.33,
      "confirmed_field_count": 5,
      "entry_count": 3,
      "dimensions": [
        {"key": "personality_social", "percent": 50.0, "evidence_count": 1},
        {"key": "intimacy_pattern", "percent": 0.0, "evidence_count": 0},
        {"key": "lifestyle", "percent": 50.0, "evidence_count": 1},
        {"key": "emotional_expression", "percent": 50.0, "evidence_count": 1},
        {"key": "relationship_boundaries", "percent": 0.0, "evidence_count": 0},
        {"key": "future_expectations", "percent": 50.0, "evidence_count": 1}
      ]
    },
    {
      "revision_id": 105,
      "revision_no": 2,
      "published_at": "2026-10-20T14:22:00Z",
      "overall_percent": 66.67,
      "confirmed_field_count": 8,
      "entry_count": 6,
      "dimensions": [
        {"key": "personality_social", "percent": 100.0, "evidence_count": 2},
        {"key": "intimacy_pattern", "percent": 50.0, "evidence_count": 1},
        {"key": "lifestyle", "percent": 100.0, "evidence_count": 3},
        {"key": "emotional_expression", "percent": 50.0, "evidence_count": 1},
        {"key": "relationship_boundaries", "percent": 50.0, "evidence_count": 1},
        {"key": "future_expectations", "percent": 100.0, "evidence_count": 2}
      ]
    }
  ],
  "has_more": false,
  "notice": "理解覆盖度仅供参考，不代表人格分或完成度。数值来自已确认画像数据。"
}
```

**成功响应（无数据/空列表）**:
```json
{
  "subject": "ideal_partner",
  "points": [],
  "has_more": false,
  "notice": "理解覆盖度仅供参考，不代表人格分或完成度。数值来自已确认画像数据。"
}
```

## 错误码

| HTTP 状态码 | 业务错误码 | 触发条件 | 前端处理建议 |
|------------|-----------|---------|------------|
| 401 | - | 未登录或 token 过期 | 跳转登录页 |
| 422 | - | 参数校验失败（subject 非法、limit 越界） | 提示参数错误 |
| 503 | AI_FEATURE_DISABLED | AI 画像功能未开放 | 显示"功能暂未开放"，入口置灰 |
| 500 | - | 服务器内部错误 | 提示"服务异常，请稍后重试" |

**错误响应示例（503）**:
```json
{
  "code": "AI_FEATURE_DISABLED",
  "message": "AI 画像功能未开放",
  "request_id": "req_abc123"
}
```

## 使用方法与业务规则

### 前置条件
- 用户已登录
- AI 画像功能已对该用户开放（通过 AI 门禁）

### 调用顺序
- 独立接口，无强制前置调用
- 通常在用户查看"画像报告"或"数据报表"页面时调用

### 幂等性
- 纯只读接口，天然幂等
- 不消耗任何额度或次数

### 频率限制
- 复用全局 API 限流策略
- 无单独画像查询额度限制

### 状态流转
- 接口返回的是**已发布版本的快照**，不返回草稿或待确认数据
- 新版本发布后，下次查询会包含该版本的快照点

### 边界场景

**场景 1：用户从未发布过画像**
- 返回：`points: []`，`has_more: false`
- 前端处理：显示空态"还没有可对比的历史版本"

**场景 2：用户只有 1 个版本**
- 返回：包含 1 个点的 `points` 数组
- 前端处理：可显示单点，但曲线图至少需要 2 个点才有意义

**场景 3：历史版本多于 limit**
- 返回：前 `limit` 个点，`has_more: true`
- 前端处理：可增加 limit 或显示"仅展示最近 N 个版本"

**场景 4：某些版本缺少快照数据**
- 原因：一期上线前的旧版本没有快照记录
- 返回：只返回有快照的版本，不补 0 值
- 前端处理：点之间用虚线连接或留空

**场景 5：用户查询非本人数据**
- 行为：登录即 200，但 `points` 为空（owner-only，不单独 404 泄露归属）
- 原因：避免通过 404 判断其他用户是否有画像

**场景 6：AI 功能未开放**
- 返回：503 + `AI_FEATURE_DISABLED`
- 前端处理：入口置灰，显示"功能暂未开放"，不伪装成空态

## 兼容性

### 向后兼容
- 一期上线前的历史版本没有快照数据，接口返回空点列表
- 前端必须能处理空数据场景，不能假设所有用户都有历史点

### 旧客户端处理
- 若前端版本未实现该接口，不影响现有功能
- 新接口为纯新增只读能力，不改变现有画像确认流程

### 字段废弃
- 本接口为一期新增，暂无废弃字段
- 未来若新增字段，会保持现有字段语义不变

## 数据口径说明

### overall_percent 计算方式
- 六维覆盖度的平均值
- 每个维度按 `calculate_journey_progress` 口径：0 个高置信证据=0%，1 个=50%，≥2 个=100%
- 公式：`sum(各维度 percent) / 6`

### confirmed_field_count 统计范围
- 仅统计 `field_kind = 'structured'` 的已确认字段
- 不包括 `entry` 类型的条目（条目计入 `entry_count`）

### dimensions 顺序
- 固定为 `PROFILE_DIMENSIONS` 常量顺序：
  1. personality_social
  2. intimacy_pattern
  3. lifestyle
  4. emotional_expression
  5. relationship_boundaries
  6. future_expectations

### null 值含义
- `overall_percent: null` → 该版本无快照数据（历史版本）
- `published_at: null` → 理论上不应出现（快照只记录已发布版本）

## 安全与隐私

### 数据可见性
- 只返回本人数据（owner-only）
- 不返回 `emotional_insight`（心理推断）等敏感字段
- 不返回原始对话、原始 prompt、用户 ID

### 越权防护
- 查询非本人数据返回空列表，不返回 404（防泄露）
- 无需额外的主体归属校验（表结构已绑定 user_id）

### 日志脱敏
- 审计日志不记录 `overall_percent` 等数值细节
- 只记录查询行为：user_id、subject、请求时间

## 前端对接建议

### 曲线图绘制
- X 轴：`published_at`（时间）
- Y 轴：`overall_percent`（0-100）
- 每条线代表一个维度的 `percent`
- 至少需要 2 个点才能绘制曲线

### 空态处理
- `points.length === 0` → 显示"还没有可对比的历史版本，继续与知遇聊天积累画像"
- `points.length === 1` → 显示"仅有 1 个版本，暂无可对比数据"

### 加载态
- 查询中显示骨架屏或加载动画
- 避免白屏或卡顿

### 错误态
- 503 → 显示"功能暂未开放"，不要显示"网络错误"
- 500 → 显示"加载失败，请稍后重试"，提供重试按钮

## 测试建议

### 单元测试覆盖
- 空数据场景（用户无历史版本）
- 单点场景（只有 1 个版本）
- 多点场景（≥2 个版本）
- 分页边界（limit=1, limit=100）
- owner-only 校验（查询非本人数据返回空）
- 503 门禁（AI_FEATURE_DISABLED）

### 契约测试
- OpenAPI path 存在性
- response_model 正确性
- 参数校验（subject 枚举、limit 范围）

### 迁移测试
- 空库 bootstrap 验证
- 已有库 up 验证
- down 回滚验证
- `scripts/check_migrations.py` 差集为空
