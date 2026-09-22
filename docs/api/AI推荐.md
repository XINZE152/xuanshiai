# 三类推荐接口（M07：我会喜欢 / 会喜欢我 / 相似的人）

接口前缀：`/api/v1/ai`。本文档是 M07 三类推荐读取接口（方案 §四 WP-P6 / §六 WP-C3 第一步）的完整契约。

### 变更记录

- 2026-09-13（第二批安全收尾）：补充**快照保留与刷新政策**（行为不变，契约显式化）：① 读取入口只复用 `status='ready'` 且未过期的快照，**不做逐读重算**；② 资料修改不立即作废旧快照——旧分与旧理由在 TTL（默认 24 小时）内继续可读；新分数经由"画像发布触发的重建任务（同日幂等，至多一单）→ worker 物化 → generation 顶替"生效；③ 读取期候选资格门（授权/投影/状态/过期）与物化候选池共享同一 SQL 条件片段（`_POOL_PROJECTION_FROM`），并有等价性测试锁定（`tests/test_ai_recommend_refresh_semantics.py`）；④ 请求人自身 `profile_text_extract` 授权撤回后：缓存保留期内旧快照仍可读，快照过期后 miss 且重建入队被既有授权检查拒绝（返回空页 + `regenerating=false`）。
- 2026-09-13：**读取期可见性与资格复检生效**。此前读取面直接返回 `status='ready'` 快照行，可见性只在物化时刻保证；现在每个候选在返回前重新过 `CandidateVisibilityService.decide(PROFILE)`（账号/审核/隐私/双向拉黑/封禁）与候选池资格门（`profile_text_extract` 授权、投影 active、状态 active、未过期），被过滤的候选即时消失，可能返回短页（少于 `limit` 条）或空列表；`rank_no` 保留物化代内名次（允许跳号）。响应结构无变化。候选资料改版（revision 前进但投影仍在）不改变可展示性，由快照 TTL（默认 24 小时）与同日幂等重建兜底。
- 2026-09-09（初版）：三视图规则打分 + `generation` 物化；i_like/likes_me 可平滑消费未过期 `engine='llm-v1'` 兼容度双向分。

通用请求头（所有接口）：

```http
Authorization: Bearer <access_token>   # 必需
```

### `GET /api/v1/ai/recommendations`

**基本信息**：读取当前用户的三类推荐快照；miss（缺失/过期/全被过滤）时入队后台重建。完整 URL `GET /api/v1/ai/recommendations`；HTTP Method `GET`；需要登录；Content-Type 响应为 `application/json`；成功状态码 `200 OK`。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `view` | query | string | 是 | — | 枚举 `i_like` / `likes_me` / `similar` | 视图：我会喜欢 / 会喜欢我 / 相似的人 |
| `limit` | query | integer | 否 | 20 | 1–50 | 返回的最大卡片数 |

请求示例：`GET /api/v1/ai/recommendations?view=i_like&limit=20`，Header `Authorization: Bearer <token>`。无请求体（非法示例：`view=hot` → 422 校验错误）。

#### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例值 |
| --- | --- | --- | --- | --- | --- |
| `view` | string | 是 | — | 回显请求的视图 | `i_like` |
| `items` | array | 是 | `[]`：无当前可展示候选 | 推荐卡列表，按 `rank_no` 升序；读取期过滤后可为短页 | 见下 |
| `items[].target_user_id` | integer | 是 | — | 候选用户 ID（前端用它调既有名片接口拼装资料） | `203` |
| `items[].score` | number \| null | 是 | `null`：无分数 | 0–100 合拍参考分（量纲与匹配度引擎一致） | `89.0` |
| `items[].coverage` | number \| null | 是 | `null`：无分数 | 0–1 双向可用维度覆盖率 | `0.8` |
| `items[].rank_no` | integer | 是 | — | 物化代内名次（从 1 起；过滤后可跳号） | `3` |
| `items[].engine` | string | 是 | — | 分数来源：`rule-v1` 规则 / `llm-v1` LLM 精算（快照混用为设计语义） | `rule-v1` |
| `items[].reason_codes` | array[string] | 是 | `[]` | 稳定原因码（如 `INTEREST_OVERLAP`、`DIMENSION_UNKNOWN`） | `["INTEREST_OVERLAP"]` |
| `items[].reason_texts` | array[string] | 是 | `[]` | 中文理由（仅 `llm-v1` 行有） | `["兴趣重合"]` |
| `regenerating` | boolean | 是 | — | `true` 表示快照 miss 且确有在途重建任务；客户端可稍后重试 | `false` |

成功响应示例（正常）：

```json
{
  "view": "i_like",
  "items": [
    {
      "target_user_id": 203,
      "score": 89.0,
      "coverage": 0.8,
      "rank_no": 1,
      "engine": "rule-v1",
      "reason_codes": ["INTEREST_OVERLAP"],
      "reason_texts": []
    }
  ],
  "regenerating": false
}
```

空态示例（无快照且重建在途）：`{"view": "similar", "items": [], "regenerating": true}`；全被过滤：`{"view": "i_like", "items": [], "regenerating": false}`。

#### 使用方法与业务规则

- 前置条件：已登录；本人已发布画像投影且持有 `profile_text_extract` 授权（否则 miss 且不入队，恒返回空页）。功能开关关闭统一 503。
- 读取期门禁：每个候选在返回前重新过可见性（双向拉黑、隐藏、封禁、账号/审核/资料完整）与授权/投影资格；**校验先于任何卡片与解释返回**。
- 短页：候选在物化后被过滤时，后续可见候选顶上（按 `rank_no` 取前 `limit` 个可见项）；无可展示项返回空 `items`。
- 幂等与频率：GET 为纯读取（miss 时的入队是唯一写入，同日幂等键 `recommend-view-{user}-{YYYYMMDD}` 收敛，重复 GET 不重复入队）。
- 快照 TTL：默认 24 小时（`ai_recommendation_ttl_minutes`）；过期视为 miss 触发重建。
- 边界：对方注销/拉黑/撤回授权后即从列表消失；`regenerating=true` 时不建议前端立即再次请求（同日任务幂等，重复 GET 不产生新任务）。

#### 错误

| HTTP | code | 触发 | retryable | 前端处理建议 |
| --- | --- | --- | --- | --- |
| 401 | — | 未登录/令牌失效 | false | 重新登录 |
| 409 | `TASK_IDEMPOTENCY_CONFLICT` | 同幂等键不同请求摘要（异常场景） | false | 稍后重试 |
| 422 | — | `view`/`limit` 校验失败 | false | 修正入参 |
| 503 | `AI_FEATURE_DISABLED` | 推荐功能开关关闭 | false | 提示功能不可用 |

错误响应统一结构：

```json
{
  "detail": {
    "code": "AI_FEATURE_DISABLED",
    "message": "推荐功能当前不可用",
    "request_id": "req_01J...",
    "retryable": false,
    "retry_after_ms": 0
  }
}
```

#### 文档自检

- [x] 请求参数表逐参数含业务含义与非法示例
- [x] 成功/空态返回示例
- [x] 返回参数表逐字段含空值含义
- [x] 使用方法与业务规则（前置条件/幂等/短页/TTL/边界）
- [x] 错误码表
