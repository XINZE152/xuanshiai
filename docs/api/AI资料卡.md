# AI 资料卡

接口前缀：`/api/v1`。本文件只覆盖「已确认画像成稿 → 资料卡开放文本草稿 → 用户显式写入个人资料」三条路径。它不是 AI 生图，也不复用 `docs/api/AI画像.md` 的会话、草稿 PATCH 或发布契约。

### 变更记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-09-22 | v1.0 | 首次公开 `POST /ai/profile-card/summarize`、`GET /ai/profile-card/draft`、`POST /ai/profile-card/draft/apply`。 |
| 2026-10-02 | v1.1（本地实施，未发布） | 变更前：客户端按最新草稿采用，缺少资料版本保护，资料写入可能提前提交。变更后：summarize 返回稳定 `draft_id`；GET 精确取稿并返回 `base_profile_revision`；apply 增加来源/资料版本校验、明确幂等冲突、标签 merge/replace_selection 与原子事务。旧客户端省略新增入参仍兼容；新版页面缺少版本信息时停止写入，禁止降级重发。 |
| 2026-10-03 | v1.1 联调说明（未发布） | 补充客户端同键同内容重试、终态任务重试与隐私可见范围说明；真实 MySQL 和静态检查不替代真实登录、UTS 编译或真机验收。 |

通用请求头：

```http
Authorization: Bearer <access_token>   # 三个接口都必需
Content-Type: application/json          # 两个写接口必需；GET 无请求体
Idempotency-Key: <8-128 位 [A-Za-z0-9._:-]>  # 仅两个写接口必需
```

通用说明：

- 三个接口都要求 `ai_profile_enabled` 通过画像功能门禁。未开启时一律 `503`，`code=AI_FEATURE_DISABLED`，不创建任务、不读草稿、不写资料。
- 三个接口都要求当前用户仍持有 `profile_text_extract` 授权。未授权或已撤回返回 `403 AI_CONSENT_REQUIRED`。
- 写接口幂等键与画像会话相同字符集，但长度是 **8–128**，不是记忆接口的 1–128。
- 响应 Content-Type 均为 `application/json`。422 参数校验错误使用 FastAPI 标准 `detail` 数组，不是上述业务错误对象；前端应区分两种结构。
- 错误响应与画像一致，包在 `detail` 里：

```json
{
  "detail": {
    "code": "AI_FEATURE_DISABLED",
    "message": "AI 画像功能当前不可用",
    "request_id": "req_01J...",
    "retryable": false,
    "retry_after_ms": 0
  }
}
```

- `profile` 成功体与 `GET /api/v1/users/me/profile` 相同，字段含义见 `docs/api/个人资料.md` §4。本文件不重复展开该对象。

---

#### 生成资料卡草稿

**基本信息**

| 项 | 值 |
| --- | --- |
| 用途 | 用本人最新已确认个人画像成稿，异步生成一份资料卡开放文本草稿 |
| URL | `POST /api/v1/ai/profile-card/summarize` |
| 登录 | 是 |
| 权限 | 画像功能开启，且已授权 `profile_text_extract` |
| Content-Type | `application/json` |
| 成功状态码 | `202` |

**请求参数**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `Authorization` | header | string | 是 | 无 | `Bearer` access token | 当前用户 |
| `Idempotency-Key` | header | string | 是 | 无 | 8–128 位，字符集 `[A-Za-z0-9._:-]` | 同一用户、同一任务类型下的回放键 |
| `force` | body | boolean | 否 | `false` | 额外字段一律拒绝 | `false`：同一成稿版本已有 `ready`/`partial` 草稿时直接回放该草稿任务，不占新额度。`true`：忽略这份可复用草稿，重新入队 |

合法示例：`force=false`。非法示例：`{"force":"maybe"}`（不可解析为布尔，422）；`{"extra":1}`（多余字段，422）；缺 `Idempotency-Key` 或 key 为 `short`（400）。客户端始终发送 JSON 布尔，不依赖服务端兼容性类型转换。

**请求体示例**

```http
POST /api/v1/ai/profile-card/summarize HTTP/1.1
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: card-key-202ok
```

```json
{
  "force": false
}
```

无 body 等价于 `{"force": false}`。

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `task_id` | string | 是 | 不适用 | 异步任务 ID，用 `GET /api/v1/ai/tasks/{task_id}` 轮询 | `"task_01J..."` |
| `draft_id` | string/null | 是 | 旧任务没有稳定草稿指针 | 本次任务对应的草稿 ID；新版客户端必须指定该 ID 取稿，不能猜最新草稿 | `"draft-ready"` |
| `status` | string | 是 | 不适用 | 任务状态，取值与 AI 任务状态枚举一致 | `"queued"` |
| `poll_url` | string | 是 | 不适用 | 轮询路径，固定为 `/api/v1/ai/tasks/{task_id}` | `"/api/v1/ai/tasks/task_01J..."` |
| `replayed` | boolean | 是 | 不适用 | 本次没有新建任务，回放了已有任务 | `false` |
| `poll_after_ms` | integer | 是 | 不适用 | 建议首次轮询等待毫秒数，服务端固定返回 `1000`，且 `>= 0` | `1000` |

**返回示例**

```json
{
  "task_id": "task_01Jabc",
  "draft_id": "draft-ready",
  "status": "queued",
  "poll_url": "/api/v1/ai/tasks/task_01Jabc",
  "replayed": false,
  "poll_after_ms": 1000
}
```

同一 key、同一成稿版本、同一 `force` 再次调用：

```json
{
  "task_id": "task_01Jabc",
  "draft_id": "draft-ready",
  "status": "queued",
  "poll_url": "/api/v1/ai/tasks/task_01Jabc",
  "replayed": true,
  "poll_after_ms": 1000
}
```

本接口无分页，也没有空列表。

**使用方法与业务规则**

- 前置条件：已登录；画像功能开启；`profile_text_extract` 仍有效；个人画像已有确认成稿（最新 `ai_profile_revision` 的叙事状态为 `confirmed`，且该版本有字段）。缺成稿返回 `400`，文案为「请先确认画像成稿」。
- 调用顺序：先完成画像确认，再生成；保存 `task_id` 和 `draft_id`，轮询成功后 `GET /ai/profile-card/draft?draft_id=<返回ID>` 并核对回执 ID。缺少草稿指针时停止，不把 202 当成可写，也不回退“最新草稿”。
- 幂等：`user + profile_card_summarize + Idempotency-Key + 请求摘要` 回放第一次任务。请求摘要包含用户、成稿版本和 `force`。同一 key 换了成稿版本或 `force`，返回 `409 TASK_IDEMPOTENCY_CONFLICT`，不会覆盖旧任务。
- `force=false` 且当前已有同一成稿版本的 `ready`/`partial` 草稿时，回放该草稿对应任务，`replayed=true`，不新建草稿、不计入额度。
- 额度：滚动 24 小时内该用户 `profile_card_summarize` 任务达到 5 次后，新的非回放请求返回 `429 AI_QUOTA_EXCEEDED`，`retryable=true`。回放不占新额度。
- 状态：新建草稿初始 `queued`、`expected_revision=1`。Worker 完成后草稿变为 `ready`；没有任何可用文本或候选时变为 `failed`。输入或输出被内容审核拒绝时任务失败，错误码 `AI_POLICY_DENIED`，草稿 `failed`。本接口本身不把草稿写成 `applied`。
- 任务状态 `failed` / `cancelled` / `superseded` 已终止时，用户明确重试需使用新操作键；轮询超时或网络结果不明则保留原键以恢复原任务。不要永久回放失败任务，也不要在未确认任务终态时重复创建。
- 边界：只使用本人成稿。理想型摘要仅在对方叙事为 `confirmed` 或 `published` 时附带，缺失不报错。功能关闭、撤权、额度用尽都不写个人资料。

**错误**

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | Idempotency-Key 缺失或不是 8–128 位允许字符；没有已确认成稿 | 换合法 key，或先完成画像确认 |
| 401 | （鉴权失败，非业务码） | 未登录或 token 无效 | 重新登录 |
| 403 | `AI_CONSENT_REQUIRED` | 未授权或已撤回 `profile_text_extract` | 引导重新授权，不要重试本请求 |
| 409 | `TASK_IDEMPOTENCY_CONFLICT` | 同一 key 对应了不同成稿版本或不同 `force` | 换新 key |
| 422 | （请求体校验失败） | `force` 类型非法或出现未声明字段 | 按 schema 修正 body |
| 429 | `AI_QUOTA_EXCEEDED` | 24 小时内新任务已达 5 次 | 稍后重试；`retryable=true` |
| 503 | `AI_FEATURE_DISABLED` | 画像功能关闭 | 停止调用，不要当成可重试故障 |

非法 key 示例：

```http
POST /api/v1/ai/profile-card/summarize
Idempotency-Key: short
```

```json
{
  "detail": {
    "code": "AI_INPUT_INVALID",
    "message": "Idempotency-Key 必须为 8-128 位 ASCII 字符",
    "request_id": "req_01J...",
    "retryable": false,
    "retry_after_ms": 0
  }
}
```

---

#### 读取资料卡草稿

**基本信息**

| 项 | 值 |
| --- | --- |
| 用途 | 按 `draft_id` 精确读取本人草稿；省略时兼容读取最新可读草稿 |
| URL | `GET /api/v1/ai/profile-card/draft` |
| 登录 | 是 |
| 权限 | 画像功能开启，且已授权 `profile_text_extract` |
| Content-Type | 无请求体 |
| 成功状态码 | `200` |

**请求参数**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `Authorization` | header | string | 是 | 无 | `Bearer` access token | 只读本人草稿 |
| `draft_id` | query | string/null | 否 | 无 | 1–64 字符；提供时精确读取该草稿，不回退到最新草稿 | 多草稿并存时的稳定选择 |

未提供 `draft_id` 时保留旧客户端行为，读取本人最新可读草稿；非法示例：`draft_id=` 为空或超过 64 字符。

**请求体示例**

无请求体。

```http
GET /api/v1/ai/profile-card/draft?draft_id=draft-ready HTTP/1.1
Authorization: Bearer <access_token>
```

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `draft_id` | string | 是 | 不适用 | 草稿 ID | `"a1b2..."` |
| `status` | string | 是 | 不适用 | `queued` / `running` / `ready` / `partial` / `applied`；不返回 `failed`/`discarded` | `"ready"` |
| `expected_revision` | integer | 是 | 不适用 | 写入时必须原样提交；新建为 1，每次成功 apply 后 +1 | `1` |
| `source_revision_id` | integer/null | 是 | 尚未绑定成稿版本 | 生成该草稿所用的个人画像 revision id | `88` |
| `base_profile_revision` | integer | 是 | 不适用 | GET 时的个人资料版本（>=0）；新版客户端采用时必须原样传回；不是草稿版本或来源 ID | `3` |
| `prompt_version` | string/null | 是 | 尚未生成 | 提示词版本 | `"profile-card-summarize-v2"` |
| `schema_version` | string/null | 是 | 尚未生成 | 草稿 schema 版本 | `"profile-card-summarize-v1"` |
| `fields` | object | 是 | 不适用 | 五个开放文本槽，见下表 |  |
| `fields.self_intro` | object | 是 | 不适用 | 自我介绍候选 |  |
| `fields.qa_1_partner` | object | 是 | 不适用 | 问答 1「理想的另一半」候选 |  |
| `fields.qa_3_love` | object | 是 | 不适用 | 问答 3「期待的爱情」候选 |  |
| `fields.qa_2_sports_candidates` | object | 是 | 不适用 | 问答 2「喜欢的运动」候选，文本在 `candidates` |  |
| `fields.interest_tag_candidates` | object | 是 | 不适用 | 兴趣标签候选，文本在 `candidates` |  |
| `fields.*.value` | string | 是 | 空串表示这一槽没有单值文案 | 模型给出的单段文本，最长按生成侧裁剪 | `"周末喜欢徒步。"` |
| `fields.*.candidates` | string[] | 是 | 空数组表示没有候选 | 只保留目录内标签；运动与兴趣槽使用它 | `["周末徒步"]` |
| `fields.*.confidence` | number | 是 | 不适用 | 0–1 | `0.8` |
| `fields.*.source_ref` | string/null | 是 | 无来源指针 | 最小来源引用，不是原文 | `null` |
| `task_id` | string/null | 是 | 草稿没有关联任务 | 生成该草稿的任务 | `"task_01Jabc"` |
| `generated_at` | string/null | 是 | 尚未生成完成 | UTC 生成时间 | `null` |
| `applied_at` | string/null | 是 | 尚未写入资料 | UTC 写入时间 | `null` |
| `applied_meta` | object/null | 是 | 尚未写入 | 写入时记录的元数据；未约定内部结构，前端不要依赖具体键 | `null` |

`status=queued` 或 `running` 时 `fields` 仍返回，各槽为空值，不代表失败。

**返回示例**

```json
{
  "draft_id": "draft-ready",
  "status": "ready",
  "expected_revision": 1,
  "source_revision_id": 88,
  "base_profile_revision": 3,
  "prompt_version": "profile-card-summarize-v2",
  "schema_version": "profile-card-summarize-v1",
  "fields": {
    "self_intro": {
      "value": "周末喜欢徒步和看展。",
      "candidates": [],
      "confidence": 0.8,
      "source_ref": null
    },
    "qa_1_partner": {"value": "", "candidates": [], "confidence": 0, "source_ref": null},
    "qa_3_love": {"value": "", "candidates": [], "confidence": 0, "source_ref": null},
    "qa_2_sports_candidates": {
      "value": "",
      "candidates": ["周末徒步"],
      "confidence": 0.7,
      "source_ref": null
    },
    "interest_tag_candidates": {
      "value": "",
      "candidates": ["周末徒步"],
      "confidence": 0.7,
      "source_ref": null
    }
  },
  "task_id": "task_01Jabc",
  "generated_at": "2026-09-22T00:23:00",
  "applied_at": null,
  "applied_meta": null
}
```

没有可读草稿时不是 200 空对象，而是 404。

**使用方法与业务规则**

- 前置条件与生成接口相同：登录、画像功能、`profile_text_extract`。
- 调用顺序：summarize 返回 202 后轮询任务，再读本接口。`queued`/`running` 可继续轮询任务，不要拿空字段去 apply。
- 读取状态为 `queued`、`running`、`ready`、`partial`、`applied`；指定 ID 时只返回本人该草稿，找不到返回 404，不回退最新草稿；省略 ID 才按更新时间取最新可读草稿。`failed`/`discarded` 均不返回。
- 幂等：只读，无 Idempotency-Key，不改状态、不占额度。
- 边界：不返回他人草稿。功能关闭时不回退成「返回旧草稿」。

**错误**

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | （鉴权失败，非业务码） | 未登录或 token 无效 | 重新登录 |
| 403 | `AI_CONSENT_REQUIRED` | 未授权或已撤回 | 重新授权后再读 |
| 404 | `PROFILE_CARD_DRAFT_NOT_FOUND` | 指定草稿缺失、属于他人、已失败/丢弃，或未提供 ID 且本人没有可读草稿 | 重新整理；不能将带 ID 请求降级成最新草稿查询 |
| 422 | （query 校验失败） | `draft_id` 为空或超过 64 字符 | 修正参数，不改成无 ID 查询 |
| 503 | `AI_FEATURE_DISABLED` | 画像功能关闭 | 停止读取 |

---

#### 采用资料卡草稿

**基本信息**

| 项 | 值 |
| --- | --- |
| 用途 | 把用户确认过的开放文本写入个人资料，并把当前草稿标为 `applied` |
| URL | `POST /api/v1/ai/profile-card/draft/apply` |
| 登录 | 是 |
| 权限 | 画像功能开启，且已授权 `profile_text_extract` |
| Content-Type | `application/json` |
| 成功状态码 | `200` |

**请求参数**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `Authorization` | header | string | 是 | 无 | Bearer token | 只写本人资料 |
| `Idempotency-Key` | header | string | 是 | 无 | 8–128 位 `[A-Za-z0-9._:-]` | 同一草稿、同一请求摘要的回放键 |
| `draft_id` | body | string/null | 否 | `null` | 1–64 字符；提供时精确采用该草稿并加行锁 | 避免多草稿串稿；省略时兼容读取最新草稿 |
| `expected_revision` | body | integer | 是 | 无 | `>= 0`，必须等于草稿当前 `expected_revision` | 草稿乐观锁 |
| `source_revision_id` | body | integer/null | 否 | `null` | `>=0`；提供且草稿已有来源时必须相等，服务端另校验草稿仍来自本人最新画像 | 新版页面必须保留 GET 的来源 ID；旧客户端可省略 |
| `base_profile_revision` | body | integer/null | 否 | `null` | `>=0`；提供时必须等于当前 `user_revision_state.profile_revision` | 资料版本乐观锁；新版页面必须原样透传 GET 快照，旧客户端可省略 |
| `tag_apply_mode` | body | string | 否 | `merge` | `merge` / `replace_selection`；后者必须同时提交 `accepted.personal_tags` | 标签合并或替换语义 |
| `accepted` | body | object | 否 | 空对象 | 禁止额外字段 | 用户确认要写入的内容 |
| `accepted.self_intro` | body | string/null | 否 | `null` | 最长 500；服务端再 strip | 要写入的自我介绍 |
| `accepted.qa_answers` | body | array/null | 否 | `null` | 每项见下表 | 要写入的「关于我」问答 |
| `accepted.personal_tags` | body | string[]/null | 否 | `null` | 必须在标签目录；超过 10 个、重复、未知标签或 merge 结果超限返回 `400 AI_INPUT_INVALID`，不截断 | 资料接口保持既有 10 个容量；S1 页面每次最多显式采用 3 个候选 |
| `rejected` | body | string[] | 否 | `[]` | 无格式枚举 | 用户明确不写的槽。只有事实列键会计入 `skipped_fields` |
| `replace_existing` | body | object | 否 | `{"self_intro": false}` | 禁止额外字段 | 是否覆盖资料里已有内容 |
| `replace_existing.self_intro` | body | boolean | 否 | `false` | 布尔 | `false` 且资料已有非空自我介绍时跳过，不覆盖 |

`accepted.qa_answers` 每项（S1 页面不提交，兼容既有客户端）：

| 字段 | 位置 | 类型 | 必填 | 默认 | 校验与含义 | 合法示例 / 非法示例 |
| --- | --- | --- | --- | --- | --- | --- |
| `question_id` | body | integer | 是 | 无 | 仅 1/2/3；对应既有关于我问题 | `1` / `4` |
| `question` | body | string/null | 否 | `null` | 最长 64；空值使用目录问题文本 | `"理想的另一半"` / 超过 64 字符 |
| `answer` | body | string | 否 | `""` | 最长 300，strip 后再内容审核；空回答不写入 | `"希望认真沟通"` / 超过 300 字符 |


非法示例：`{"expected_revision": -1}`（422）；`accepted` 里带 `height`（422，该对象禁止额外字段）；缺 Idempotency-Key（400）。`rejected` 里出现 `height` **不是**非法请求，它只表示不写该事实列。

**请求体示例**

```http
POST /api/v1/ai/profile-card/draft/apply HTTP/1.1
Authorization: Bearer <access_token>
Content-Type: application/json
Idempotency-Key: apply-key-0001
```

```json
{
  "expected_revision": 1,
  "draft_id": "draft-ready",
  "source_revision_id": 88,
  "base_profile_revision": 3,
  "tag_apply_mode": "merge",
  "accepted": {
    "self_intro": "周末喜欢徒步和看展，希望找一个能一起出门的人。",
    "qa_answers": [
      {"question_id": 1, "answer": "希望对方也喜欢出门。"}
    ],
    "personal_tags": ["周末徒步"]
  },
  "rejected": ["height", "education", "income"],
  "replace_existing": {"self_intro": false}
}
```

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `status` | string | 是 | 不适用 | 恒为 `applied` | `"applied"` |
| `replayed` | boolean | 是 | 不适用 | 同一 key 与同一请求摘要命中已应用草稿时为 `true` | `false` |
| `draft_id` | string | 是 | 不适用 | 实际采用的草稿 ID | `"draft-ready"` |
| `expected_revision` | integer | 是 | 不适用 | 本次成功采用后的最终草稿 revision | `2` |
| `written_fields` | string[] | 是 | 空数组表示这次没有新写入 | 实际写入的槽 | `["self_intro","personal_tags"]` |
| `skipped_fields` | string[] | 是 | 空数组表示没有跳过 | 因已有内容、空文本或被拒绝的事实列而没写的槽 | `["height"]` |
| `profile` | object/null | 是 | 无 | 写入后的个人资料；结构同 `GET /users/me/profile` | 见个人资料文档 |

**返回示例**

```json
{
  "status": "applied",
  "replayed": false,
  "draft_id": "draft-ready",
  "expected_revision": 2,
  "written_fields": ["self_intro", "qa_1", "personal_tags"],
  "skipped_fields": ["height", "education", "income"],
  "profile": {
    "self_intro": "周末喜欢徒步和看展，希望找一个能一起出门的人。"
  }
}
```

上面的 `profile` 只示范本接口会带上资料对象；其余字段与空值含义以 `docs/api/个人资料.md` §4 为准，不在这里另造一份。

同一 key、同一 body 再调用一次时，`replayed=true`，`written_fields` / `skipped_fields` / `profile` 回放第一次落库的响应。

**使用方法与业务规则**


- 调用顺序：先 GET draft；多草稿并存时把返回的 `draft_id` 和 `expected_revision` 原样带回。用户必须在客户端改过或确认过文本后再提交。
- 幂等：草稿已是 `applied`，且 Idempotency-Key 与请求摘要都和上次相同，回放第一次响应，不再写资料、不再把 `expected_revision` +1。回执始终包含实际 `draft_id` 与最终 `expected_revision`。
- 相同草稿的同一幂等键携带不同 body 摘要，返回 `409 TASK_IDEMPOTENCY_CONFLICT`，不会覆盖已落库回执。回放的资料是第一次成功时的快照，不代表此后资料的当前值。
- 乐观锁：`expected_revision`、已提供的 `source_revision_id`、已提供的 `base_profile_revision` 任一不一致返回 `409 DRAFT_VERSION_CONFLICT`。
- 来源失效：采用前以当前读检查本人最新画像 revision；草稿来源不再是最新版本时返回 `409 RESULT_STALE`，必须重新整理。不能仅把旧草稿来源 ID 原样提交来绕过校验。
- 标签：默认 `merge`；`replace_selection` 用本次 `personal_tags` 完整替换已有选择。输入超过 10 个、合并结果超过 10 个或替换模式缺少选择返回 `400 AI_INPUT_INVALID`，服务端不静默截断。
- 事务：资料写入（包括 profile revision/outbox）、草稿 `applied` 状态、幂等回执在同一事务中；提交前任一步失败全部回滚，不留下部分资料变更、revision 增量或 applied 标记。数据库提交确认或 HTTP 响应丢失不能推断“未写入”，必须保留原键和 body 核对回执。
- 锁顺序：资料编辑与采用复用用户行锁；采用再锁草稿、当前来源和资料 revision。GET 仅返回快照，不持有写锁。
- S1 页面仅展示可编辑自我介绍与逐项选择的最多 3 个标签；问答槽保留既有服务端兼容能力，但不纳入 S1 采用入口。
- 重试：首次提交时冻结已确认文本、标签、替换选项、草稿/来源/资料版本及操作键。网络或 5xx 结果不明时锁定编辑并原样重试；不能同键改 body，也不能删除约束重发。首次明确 400/422 且此前从未出现结果不明时，允许修改内容后重新确认并使用新键。
- 可见范围：采用不会变更本人既有隐私设置；对方仍按现有可见性策略读取。回放回执是首次成功快照，若要判断当前资料或移除是否生效，必须重新读取当前资料，不以旧回执覆盖当前状态。
- 额度：本接口不占 summarize 的每日 5 次额度。
- 边界：不能写他人草稿。功能关闭时不能借此接口清理或补写。重复提交同一已应用请求应看 `replayed`，不要据此再改 UI 草稿。

**错误**

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | key 非法；草稿还不能写；标签超限/重复/目录外；replace_selection 缺少 personal_tags；自我介绍或问答被审核拒绝 | 首次明确拒绝时修改后重新确认；若此前结果不明，先核对原请求结果 |
| 401 | （鉴权失败，非业务码） | 未登录或 token 无效 | 重新登录 |
| 403 | `AI_CONSENT_REQUIRED` | 授权已撤回 | 重新授权 |
| 404 | `PROFILE_CARD_DRAFT_NOT_FOUND` | 没有指定或可读草稿 | 重新 GET 或 summarize |
| 409 | `DRAFT_VERSION_CONFLICT` | 草稿、来源成稿或资料版本不一致 | 重新 GET 指定 draft_id 后再提交 |
| 409 | `RESULT_STALE` | 草稿来源不再是本人最新画像 | 重新整理，不复用旧稿 |
| 409 | `TASK_IDEMPOTENCY_CONFLICT` | 同一草稿的同一幂等键对应不同 body | 保留已成功回执；核对后以新操作键提交 |
| 422 | （请求体校验失败） | revision 缺失/为负、枚举非法、`accepted` 含未声明字段 | 按字段表修正 |
| 503 | `AI_FEATURE_DISABLED` | 画像功能关闭 | 停止写入 |
| 503 | `AI_TEMPORARILY_UNAVAILABLE` | 事务提交或写入过程出现未分类异常 | 不猜测未写入；保留原 key、精确草稿 ID、全部版本与相同 body 重试核对回执 |

非法 body 示例：

```json
{
  "expected_revision": 1,
  "accepted": {"height": 180}
}
```

`accepted` 不允许 `height`，返回 422。身高只能出现在 `rejected`，并且仍然不会被写入。
