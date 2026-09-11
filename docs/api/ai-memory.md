# AI 记忆内核接口（Memory Kernel Core v1，Shadow 阶段）

> 变更记录
>
> | 日期 | 版本 | 说明 |
> | --- | --- | --- |
> | 2026-09-05 | v1.0 | 记忆内核核心 v1：列表、确认、纠正、删除（墓碑）、解除墓碑。本期为 Shadow 阶段，不切换任何下游读取。 |
> | 2026-09-07 | v1.1 | `items[].revision` 正式公开为写操作乐观锁版本；分页 cursor 绑定当前用户、subject/status 筛选并在 15 分钟后过期。旧 cursor 不能跨版本复用。 |
> | 2026-09-09 | v1.2 | 统一写操作 Idempotency-Key 的 trim、长度与空白/控制字符校验；补充单个投影授权撤销契约。 |

### 错误码速查

| 错误码 | HTTP | 触发条件 |
| --- | --- | --- |
| `AI_INPUT_INVALID` | 400/422 | cursor 非法、请求体校验失败、未知状态值、缺少 Idempotency-Key |
| `AI_POLICY_DENIED` | 403 | 写入违反记忆策略（subject/fact_kind 互锁、墓碑阻断） |
| `MEMORY_CLAIM_NOT_FOUND` | 404 | Claim 不存在或属于他人（owner 隔离） |
| `MEMORY_SUPPRESSION_NOT_FOUND` | 404 | 墓碑不存在或属于他人 |
| `MEMORY_REVISION_CONFLICT` | 409 | expected_revision 与服务端 revision 不一致（乐观锁） |
| `MEMORY_CLAIM_STATE_DENIED` | 409 | Claim 当前状态不允许该动作（如 confirmed 后再确认） |
| `MEMORY_IDEMPOTENCY_CONFLICT` | 409 | 同一 Idempotency-Key 被用于不同请求内容 |

## 1. 查询记忆条目

`GET /api/v1/ai/memory`

### 基本信息

- **用途**：列出当前登录用户自己的长期记忆条目（Claim 视图），供后续 Memory View 使用。
- **是否登录**：是（Bearer Token）。
- **Content-Type**：无请求体。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| query | subject | string | 是 | - | `personal` / `ideal_partner` | 记忆主体 |
| query | status | string | 否 | 全部状态 | `proposed/confirmed/superseded/contradicted/user_corrected/expired` 或 `active`（= proposed+confirmed 别名） | 按状态过滤 |
| query | cursor | string | 否 | - | 服务端签发的签名 cursor（≤512 字符、15 分钟有效），必须携带签发时相同的 `subject`/`status` | 分页续拉；伪造、过期、跨用户或改变筛选条件复用一律 400 |
| query | limit | int | 否 | 20 | 1–50 | 每页条数 |

### 请求体示例

无请求体。非法示例：`GET /api/v1/ai/memory?subject=third_party` → 422（subject 只允许两个主体）。

### 返回参数

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 |
| --- | --- | --- | --- | --- |
| items | array | 是 | - | 记忆条目列表 |
| items[].claim_id | string | 是 | - | 条目（Claim）ID |
| items[].subject | string | 是 | - | `personal` / `ideal_partner` |
| items[].node_type | string | 是 | - | 恒为 `claim` |
| items[].content | string | 否 | 结构化字段无文本 | 条目文本（人类可读形态） |
| items[].value | any | 否 | 条目无值 | 规范化值 |
| items[].status | string | 是 | - | Claim 状态机当前状态 |
| items[].confidence | float | 否 | AI 未给出 | 0–1 置信度 |
| items[].stability | float | 是 | - | 0–1 稳定度 |
| items[].importance | float | 否 | 默认 0.5 | 重要度；仅用户确认后 `importance_confirmed` 为真 |
| items[].source_quote | string | 否 | 无摘录 | ≤512 字符的最小原文摘录 |
| items[].source_ref | string | 否 | 无引用 | 最小来源指针（如 `candidate:<id>`） |
| items[].canonical_key | string | 是 | - | 事实规范化键 |
| items[].revision | int | 是 | - | 条目当前 `last_event_seq`；confirm/correct 必须原样作为 `expected_revision` 提交 |
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
      "status": "proposed",
      "confidence": 0.82,
      "stability": 0.9,
      "importance": 0.5,
      "source_quote": "我每天早上都要喝一杯咖啡",
      "source_ref": "candidate:abc123",
      "canonical_key": "personal:lifestyle:3f2a19c0d4e5b6a7",
      "revision": 7
    }
  ],
  "next_cursor": "<server-signed-opaque-cursor>",
  "has_more": true
}
```

分页说明：`items`/`next_cursor`/`has_more`；cursor 由服务端 HMAC 签名签发，
`after_seq` 单调推进。它绑定 owner、subject 与 status，并在 15 分钟后失效；
筛选条件改变时从首页重新拉取，篡改或跨用户复用返回 400。

### 使用方法与业务规则

- 前置条件：登录；仅返回当前登录用户自己的记忆（owner 隔离，他人条目不可见）。
- 调用顺序：先调用本接口拿到 `claim_id` 与 `revision`，再调用确认/纠正/删除。
- 幂等：GET 天然幂等。
- 频率限制：暂无独立限流；遵守 AI 域通用限流策略。
- 状态流转：见「8. 状态流转与兼容策略」。
- 边界场景：无任何记忆时返回空 `items`；`has_more=false` 时 `next_cursor` 为 null。
- 删除生效语义：canonical key 命中活动墓碑（用户已删除）的条目不出现在列表中；解除墓碑后恢复展示。`status` 参数只筛选 Claim 自身状态，墓碑过滤始终生效。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | cursor 伪造/过期/跨用户 | 重置分页，从第一页重新拉取 |
| 401 | - | 未登录或登录失效 | 跳转登录 |
| 422 | `AI_INPUT_INVALID` | subject/status/limit 非法 | 修正参数 |

## 2. 确认记忆条目

`POST /api/v1/ai/memory/{claim_id}/confirm`

### 基本信息

- **用途**：用户确认一条 proposed 记忆为 confirmed，并可设定重要度/硬约束。这是唯一能把 `importance_confirmed` 置为真的入口。
- **是否登录**：是。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | claim_id | string | 是 | 1–64 | 条目 ID |
| header | Idempotency-Key | string | 是 | 首尾空白 trim 后 1–128；不得含内部空白或控制字符 | 幂等键；同一 key 重试返回同一结果 |
| body | expected_revision | int | 是 | ≥1 | 客户端持有的乐观锁版本（列表返回的 `revision`） |
| body | importance | float | 是 | 0–1 | 用户确认的重要度 |
| body | constraint_type | string | 否 | ≤32 | 硬约束/偏好类型标注 |

### 请求体示例

```json
{"expected_revision": 7, "importance": 0.9, "constraint_type": "preference"}
```

非法示例：`{"expected_revision": 7}` → 422（缺 importance）。

### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
| --- | --- | --- | --- |
| claim_id | string | 是 | 条目 ID |
| event_id | string | 是 | 本次确认生成的事件 ID（回放时为原事件 ID） |
| status | string | 是 | 恒为 `confirmed` |
| revision | int | 是 | 确认后的事件序号（下一次乐观锁基准） |
| importance_confirmed | bool | 是 | 恒为 true |

### 返回示例

```json
{"claim_id": "clm_9f2c...e1", "event_id": "7c1f...ab", "status": "confirmed", "revision": 8, "importance_confirmed": true}
```

### 使用方法与业务规则

- 前置条件：Claim 状态为 `proposed` 且属于当前用户。
- 幂等：同一 Idempotency-Key 重试回放同一事件，不追加、不再物化。
- 并发：expected_revision 不匹配 → 409，前端需重新拉取后再试。
- AI 推荐的重要度不能通过任何接口注入 `importance_confirmed`。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `MEMORY_CLAIM_NOT_FOUND` | 不存在或他人条目 | 刷新列表 |
| 409 | `MEMORY_REVISION_CONFLICT` | 乐观锁冲突 | 拉取最新 revision 后重试 |
| 409 | `MEMORY_CLAIM_STATE_DENIED` | 非 proposed 状态 | 展示当前状态 |
| 409 | `MEMORY_IDEMPOTENCY_CONFLICT` | 同 key 不同内容 | 换新 Idempotency-Key |

## 3. 纠正记忆条目

`POST /api/v1/ai/memory/{claim_id}/correct`

### 基本信息

- **用途**：用户纠正事实值；旧值不删除，纠正事件携带因果链，Claim 行转为 `user_corrected`。
- **是否登录**：是。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | claim_id | string | 是 | 1–64 | 条目 ID |
| header | Idempotency-Key | string | 是 | 首尾空白 trim 后 1–128；不得含内部空白或控制字符 | 幂等键 |
| body | expected_revision | int | 是 | ≥1 | 乐观锁版本 |
| body | value | any | 是 | 按 subject 白名单校验 | 纠正后的值 |
| body | importance | float | 否 | 0–1 | 顺带更新重要度（不改变 importance_confirmed） |
| body | constraint_type | string | 否 | ≤32 | 顺带更新约束类型 |
| body | source_quote | string | 否 | ≤512 | 纠正依据的最小原文摘录 |

### 请求体示例

```json
{"expected_revision": 8, "value": "基本不喝咖啡", "source_quote": "其实我最近戒了"}
```

非法示例：`{"expected_revision": 8}` → 422（缺 value）。

### 返回参数

同「2. 确认记忆条目」，`status` 恒为 `user_corrected`，无 `importance_confirmed` 字段。

### 返回示例

```json
{"claim_id": "clm_9f2c...e1", "event_id": "aa31...bc", "status": "user_corrected", "revision": 9}
```

### 使用方法与业务规则

- 前置条件：Claim 状态为 `proposed` 或 `confirmed`；纠正不覆盖旧 Claim（旧值保留在事件账本，可回放追溯）。
- 幂等/并发：同「2. 确认记忆条目」。
- 纠正后依赖该 Claim 的未确认 Insight 由派生 Worker 自动失效。

### 错误

同「2. 确认记忆条目」。

## 4. 删除记忆条目（生成墓碑）

`POST /api/v1/ai/memory/{claim_id}/suppress`

### 基本信息

- **用途**：用户删除一条记忆；对该事实的 canonical key 生成墓碑，阻止同 key 自动重抽取。
- **是否登录**：是。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | claim_id | string | 是 | 1–64 | 条目 ID |
| header | Idempotency-Key | string | 是 | 首尾空白 trim 后 1–128；不得含内部空白或控制字符 | 幂等键 |
| body | reason | string | 否 | ≤200 | 删除原因（可空） |

### 请求体示例

```json
{"reason": "不再准确"}
```

非法示例：`{}`（无 Idempotency-Key 头）→ 422。

### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
| --- | --- | --- | --- |
| suppression_id | string | 是 | 墓碑 ID（`sup_<event_id>`），用于解除 |
| event_id | string | 是 | 事件 ID |
| status | string | 是 | 恒为 `suppressed` |

### 返回示例

```json
{"suppression_id": "sup_32bf...2bae", "event_id": "32bf...2bae", "status": "suppressed"}
```

### 使用方法与业务规则

- 前置条件：Claim 属于当前用户；同一 canonical key 已有活动墓碑时幂等返回。
- 状态流转：墓碑 `active` →（解除）`lifted`；解除后可再次删除（新的用户意图，新的 Idempotency-Key）。
- 墓碑只阻断自动重抽取，不影响用户主动重新声明。
- 删除对用户可见生效：墓碑活动期间 GET `/memory` 不再返回该条目；解除后恢复。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `MEMORY_CLAIM_NOT_FOUND` | 不存在或他人条目 | 刷新列表 |

## 5. 解除删除墓碑

`POST /api/v1/ai/memory/suppressions/{suppression_id}/lift`

### 基本信息

- **用途**：解除用户自己的删除墓碑，恢复该事实的自动沉淀。
- **是否登录**：是。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | suppression_id | string | 是 | 1–64 | 墓碑 ID |
| header | Idempotency-Key | string | 是 | 首尾空白 trim 后 1–128；不得含内部空白或控制字符 | 幂等键 |

### 请求体示例

无请求体（`{}` 可省略）。非法示例：无 Idempotency-Key 头 → 422。

### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
| --- | --- | --- | --- |
| suppression_id | string | 是 | 墓碑 ID |
| event_id | string | 是 | 解除事件 ID |
| status | string | 是 | 恒为 `lifted` |

### 返回示例

```json
{"suppression_id": "sup_32bf...2bae", "event_id": "604a...7c2", "status": "lifted"}
```

### 使用方法与业务规则

- 权限：只能解除当前登录用户自己的墓碑（他人墓碑一律 404，不泄露存在性）。
- 幂等：同 key 重试回放；已 lifted 的墓碑重复解除为幂等无操作。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `MEMORY_SUPPRESSION_NOT_FOUND` | 不存在或他人墓碑 | 刷新墓碑列表 |

## 6. 撤销单个投影授权

`POST /api/v1/ai/memory/grants/{grant_id}/revoke`

### 基本信息

- **用途**：撤销当前登录用户的单个投影授权，并立即使对应 active 投影失效。
- **是否登录**：是。
- **成功状态码**：200。

### 请求参数

| 位置 | 参数 | 类型 | 必填 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| path | grant_id | string | 是 | 1–96 | 当前用户拥有的投影授权 ID |
| header | Idempotency-Key | string | 是 | 首尾空白 trim 后 1–128；不得含内部空白或控制字符 | 统一写操作输入门禁 |

### 请求体示例

无请求体。非法示例：缺少 `Idempotency-Key` 或使用 `key value` → 422。

### 返回参数

| 字段 | 类型 | 必返 | 业务含义 |
| --- | --- | --- | --- |
| grant_id | string | 是 | 被撤销的授权 ID |
| status | string | 是 | `revoked` 或 `already_revoked` |
| function_key | string | 是 | 授权功能标识 |
| purpose | string | 是 | 授权用途 |
| data_category | string | 是 | 授权数据类别 |
| invalidated_projections | int | 是 | 本次失效的 active 投影数量 |

### 使用方法与业务规则

- 仅能撤销自己的授权；不存在或他人授权返回 404，不泄露存在性。
- `Idempotency-Key` 在本端点只作为统一输入门禁，不建立独立操作账本，也不按 key 承诺回放或冲突语义。
- 撤销状态机天然幂等：首次返回 `revoked` 并使投影失效；任何后续重复撤销（无论 key 是否相同）返回 `already_revoked`，不会再次递增 privacy revision 或再次失效。

### 错误

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 404 | `MEMORY_GRANT_NOT_FOUND` | 授权不存在或属于他人 | 刷新授权列表 |
| 422 | `AI_INPUT_INVALID` | Idempotency-Key 缺失、长度非法、含内部空白或控制字符 | 生成合法新 key 后重试 |

## 8. 状态流转与兼容策略

### 8.1 Claim 状态机（冻结）

`proposed → confirmed → superseded / contradicted / user_corrected / expired`；
`user_corrected` 与 `expired` 为终态，不再接受确认/纠正。

### 8.2 Suppression 状态机（冻结）

`active → lifted`；lifted 后可重新激活（新事件）。

### 8.3 兼容策略

- 本期为 Shadow 阶段：本组接口全部为**新增**，不影响任何既有接口与前端；
  Search / Compatibility / Recommend 仍读取既有画像投影，不读取记忆内核。
- 所有响应只包含最小字段；完整 transcript 不进入任何响应、日志或事件 payload。
- 后续若开放 Memory View 前端，仅在本组接口之上扩展，不修改既有字段语义。
