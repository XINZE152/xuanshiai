# AI 记忆消费与授权管理接口（Phase 3：Memory View + 投影授权撤销）

> 本文档覆盖 Phase 3 新增的两个端点；Core v1 的记忆条目端点（`GET /ai/memory`、
> confirm / correct / suppress / lift）见 `docs/api/ai-memory.md`。2026-09-07 起，
> 视图条目也包含 `revision`，分页 cursor 绑定筛选条件并在 15 分钟后失效。
> 路由前缀：`/api/v1/ai`（下文省略）。鉴权与错误 envelope 与记忆内核一致。

### 错误码速查

| 错误码 | HTTP | 触发场景 |
| --- | --- | --- |
| `AI_INPUT_INVALID` | 400 / 422 | cursor 伪造或非法参数 / 缺少 Idempotency-Key |
| `MEMORY_GRANT_NOT_FOUND` | 404 | 授权不存在或不属于当前用户（owner-scoped，不泄露他人资源存在性） |
| `AI_POLICY_DENIED` | 403 | 授权维度违反冻结策略 |

---

## 1. 记忆管理页视图（Memory View）

`GET /api/v1/ai/memory/view`

### 基本信息

- **用途**：一次返回当前用户跨主体（本人 + 伴侣偏好）的记忆条目与全部投影授权
  概览，供「记忆管理页」渲染条目列表与授权开关。Phase 4 前端接入时以本文档为准。
- **是否登录**：是（Bearer Token）。
- **Content-Type**：无请求体。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| query | subject | string | 否 | 两个主体都返回 | `personal` / `ideal_partner` | 按主体过滤 |
| query | status | string | 否 | 全部状态 | `proposed/confirmed/superseded/contradicted/user_corrected/expired` 或 `active`（= proposed+confirmed 别名） | 按状态过滤 |
| query | cursor | string | 否 | - | 服务端签发签名 cursor（≤512 字符、15 分钟有效）；后续请求必须保持相同的 `subject`/`status` | 分页续拉；伪造、过期、跨用户或变更筛选复用一律 400 |
| query | limit | int | 否 | 20 | 1–50 | 每页条数 |

### 请求体示例

无请求体。非法示例：`GET /api/v1/ai/memory/view?subject=third_party` → 422。

### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 |
| --- | --- | --- | --- | --- |
| items | array | 是 | - | 记忆条目列表（跨主体） |
| items[].claim_id | string | 是 | - | 条目（Claim）ID |
| items[].subject | string | 是 | - | `personal` / `ideal_partner` |
| items[].node_type | string | 是 | - | 恒为 `claim` |
| items[].content | string | 否 | 结构化字段无文本 | 条目文本（人类可读形态） |
| items[].value | any | 否 | 条目无值 | 规范化值 |
| items[].status | string | 是 | - | Claim 状态机当前状态 |
| items[].confidence | float | 否 | AI 未给出 | 0–1 置信度 |
| items[].stability | float | 是 | - | 0–1 稳定度 |
| items[].importance | float | 否 | 默认 0.5 | 重要度 |
| items[].source_quote | string | 否 | 无摘录 | ≤512 字符的最小原文摘录 |
| items[].source_ref | string | 否 | 无引用 | 最小来源指针 |
| items[].canonical_key | string | 是 | - | 事实规范化键 |
| items[].updated_at | string | 是 | - | 条目最近更新时间（ISO 8601，无时区后缀） |
| items[].revision | int | 是 | - | 当前 `last_event_seq`；传入 confirm/correct 的 `expected_revision`，不得由客户端猜测 |
| grants | array | 是 | - | 投影授权概览（含 revoked 历史，用于渲染授权状态） |
| grants[].grant_id | string | 是 | - | 授权 ID（不透明、稳定、最长 96 字符；只能使用接口返回值） |
| grants[].function_key | string | 是 | - | 消费方：`search` / `compatibility` / `recommend` / `counselor_context` / `persona_context` |
| grants[].purpose | string | 是 | - | 消费用途（`candidate_filter` 等） |
| grants[].data_category | string | 是 | - | 数据类别（`personal_profile` / `ideal_partner_preference` / `compatibility_features` / `public_profile_summary`） |
| grants[].status | string | 是 | - | `active` / `revoked` |
| grants[].policy_revision | string | 是 | - | 授权时策略版本 |
| grants[].granted_at | string | 是 | - | 授予时间 |
| grants[].revoked_at | string | 否 | 未撤销 | 撤销时间 |
| next_cursor | string | 否 | 已到末页 | 下一页签名 cursor |
| has_more | bool | 是 | - | 是否还有下一页 |

### 返回示例

```json
{
  "items": [
    {
      "claim_id": "clm_9f2c...e1",
      "subject": "personal",
      "node_type": "claim",
      "content": "每天喝咖啡",
      "value": "每天喝咖啡",
      "status": "confirmed",
      "confidence": 0.82,
      "stability": 0.9,
      "importance": 0.5,
      "source_quote": "我每天早上都要喝一杯咖啡",
      "source_ref": "candidate:abc123",
      "canonical_key": "personal:lifestyle:3f2a19c0d4e5b6a7",
      "updated_at": "2026-09-06T08:00:00",
      "revision": 7
    }
  ],
  "grants": [
    {
      "grant_id": "prj-grant:42:search:candidate_filter:personal_profile",
      "function_key": "search",
      "purpose": "candidate_filter",
      "data_category": "personal_profile",
      "status": "active",
      "policy_revision": "ai-policy-2026-08-07-v1",
      "granted_at": "2026-09-06T08:00:00",
      "revoked_at": null
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

### 使用方法与业务规则

- 前置条件：登录；仅返回当前登录用户自己的数据（owner 隔离，他人条目与授权不可见）。
- 响应边界：完整 transcript、Provider 响应、他人私密字段不出现在任何字段中；
  `source_quote` 为 ≤512 字符最小摘录。
- 授权语义：`grants` 反映 AI 消费方当前可读取的维度；`profile_text_extract`
  授权授予时由服务端自动创建全部 8 个维度授权；用户可对单个授权调用撤销接口。
- 幂等：GET 天然幂等。
- 分页：后续请求必须原样携带当前筛选项；切换主体、状态或 cursor 过期时从第一页重新拉取。
- 边界场景：无任何记忆时 `items` 为空数组；无任何授权（未授予
  `profile_text_extract`）时 `grants` 为空数组。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | cursor 伪造/过期/跨用户 | 重置分页，从第一页重新拉取 |
| 401 | - | 未登录或登录失效 | 跳转登录 |
| 422 | `AI_INPUT_INVALID` | subject/status/limit 非法 | 修正参数 |

---

## 2. 撤销投影授权

`POST /api/v1/ai/memory/grants/{grant_id}/revoke`

### 基本信息

- **用途**：撤销当前用户的一个投影授权；该维度所有 active 投影立即失效，
  对应 AI 消费方（搜索/兼容性/推荐/AI 军师/AI 分身）即刻读取不到该维度。
- **是否登录**：是（Bearer Token）。
- **Content-Type**：无请求体。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | grant_id | string | 是 | ≤96 字符；使用视图接口返回的授权 ID | 要撤销的授权 ID（从视图接口 `grants[].grant_id` 获取） |
| header | Idempotency-Key | string | 是 | 非空 | 幂等键；重复请求返回 `already_revoked`，不产生副作用 |

### 请求体示例

无请求体。

### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
| --- | --- | --- | --- |
| grant_id | string | 是 | 回显授权 ID |
| status | string | 是 | `revoked`（本次撤销生效）/ `already_revoked`（重复请求，无副作用） |
| function_key | string | 是 | 被撤销的消费维度 |
| purpose | string | 是 | 被撤销的消费用途 |
| data_category | string | 是 | 被撤销的数据类别 |
| invalidated_projections | int | 是 | 本次撤销立即失效的 active 投影条数（`already_revoked` 时为 0） |

### 请求 / 响应示例

```
POST /api/v1/ai/memory/grants/prj-grant:42:search:candidate_filter:personal_profile/revoke
Headers: Idempotency-Key: revoke-20260906-1
```

```json
{
  "grant_id": "prj-grant:42:search:candidate_filter:personal_profile",
  "status": "revoked",
  "function_key": "search",
  "purpose": "candidate_filter",
  "data_category": "personal_profile",
  "invalidated_projections": 2
}
```

### 使用方法与业务规则

- owner 隔离：只能撤销自己的授权；他人授权与不存在的授权同样返回 404
  （不泄露资源存在性）。
- 撤销即时生效：同事务内撤销授权并失效该维度全部 active 投影；随后递增
  privacy revision 向下游传播（消费方缓存/读路径据此失效）。
- 恢复方式：重新走 `profile_text_extract` 授权授予流程（服务端自动重建全部
  维度授权）；不存在"恢复单个授权"的接口。
- 幂等：重复请求返回 `already_revoked`，不再重复失效、不再递增 revision。
- 与授权生产者的关系：挂起的授权生产者重试事件携带旧 privacy revision 向量，
  撤权递增后按 superseded 收口，不会复活用户手动撤销的授权。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `MEMORY_GRANT_NOT_FOUND` | 授权不存在 / 属于他人 | 刷新视图；提示授权已不存在 |
| 401 | - | 未登录或登录失效 | 跳转登录 |
| 403 | `AI_POLICY_DENIED` | 授权行违反冻结策略 | 提示暂无法操作，联系客服 |
| 422 | `AI_INPUT_INVALID` | 缺少 Idempotency-Key | 补充请求头后重试 |
