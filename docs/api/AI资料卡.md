# AI 资料卡

接口前缀：`/api/v1`。本文件只覆盖「已确认画像成稿 → 资料卡开放文本草稿 → 用户显式写入个人资料」三条路径。它不是 AI 生图，也不复用 `docs/api/AI画像.md` 的会话、草稿 PATCH 或发布契约。

### 变更记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-09-22 | v1.0 | 首次公开 `POST /ai/profile-card/summarize`、`GET /ai/profile-card/draft`、`POST /ai/profile-card/draft/apply`。 |

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

合法示例：`force=false`。非法示例：`{"force":"yes"}`（类型不符，422）；`{"extra":1}`（多余字段，422）；缺 `Idempotency-Key` 或 key 为 `short`（400）。

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
| `status` | string | 是 | 不适用 | 任务状态，取值与 AI 任务状态枚举一致 | `"queued"` |
| `poll_url` | string | 是 | 不适用 | 轮询路径，固定为 `/api/v1/ai/tasks/{task_id}` | `"/api/v1/ai/tasks/task_01J..."` |
| `replayed` | boolean | 是 | 不适用 | 本次没有新建任务，回放了已有任务 | `false` |
| `poll_after_ms` | integer | 是 | 不适用 | 建议首次轮询等待毫秒数，服务端固定返回 `1000`，且 `>= 0` | `1000` |

**返回示例**

```json
{
  "task_id": "task_01Jabc",
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
  "status": "queued",
  "poll_url": "/api/v1/ai/tasks/task_01Jabc",
  "replayed": true,
  "poll_after_ms": 1000
}
```

本接口无分页，也没有空列表。

**使用方法与业务规则**

- 前置条件：已登录；画像功能开启；`profile_text_extract` 仍有效；个人画像已有确认成稿（最新 `ai_profile_revision` 的叙事状态为 `confirmed`，且该版本有字段）。缺成稿返回 `400`，文案为「请先确认画像成稿」。
- 调用顺序：先完成画像确认，再调用本接口；拿到 `task_id` 后轮询任务；任务成功后再 `GET /ai/profile-card/draft`。不要把 202 当成草稿已可写。
- 幂等：`user + profile_card_summarize + Idempotency-Key + 请求摘要` 回放第一次任务。请求摘要包含用户、成稿版本和 `force`。同一 key 换了成稿版本或 `force`，返回 `409 TASK_IDEMPOTENCY_CONFLICT`，不会覆盖旧任务。
- `force=false` 且当前已有同一成稿版本的 `ready`/`partial` 草稿时，回放该草稿对应任务，`replayed=true`，不新建草稿、不计入额度。
- 额度：滚动 24 小时内该用户 `profile_card_summarize` 任务达到 5 次后，新的非回放请求返回 `429 AI_QUOTA_EXCEEDED`，`retryable=true`。回放不占新额度。
- 状态：新建草稿初始 `queued`、`expected_revision=1`。Worker 完成后草稿变为 `ready`；没有任何可用文本或候选时变为 `failed`。输入或输出被内容审核拒绝时任务失败，错误码 `AI_POLICY_DENIED`，草稿 `failed`。本接口本身不把草稿写成 `applied`。
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
| 用途 | 读取本人最新一份未丢弃的资料卡草稿，供用户确认后再写入资料 |
| URL | `GET /api/v1/ai/profile-card/draft` |
| 登录 | 是 |
| 权限 | 画像功能开启，且已授权 `profile_text_extract` |
| Content-Type | 无请求体 |
| 成功状态码 | `200` |

**请求参数**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `Authorization` | header | string | 是 | 无 | `Bearer` access token | 只读本人草稿 |

无 query、无 body。非法示例：未带 token。

**请求体示例**

无请求体。

```http
GET /api/v1/ai/profile-card/draft HTTP/1.1
Authorization: Bearer <access_token>
```

**返回参数**

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `draft_id` | string | 是 | 不适用 | 草稿 ID | `"a1b2..."` |
| `status` | string | 是 | 不适用 | `queued` / `running` / `ready` / `partial` / `applied` / `failed`。读取不返回 `discarded` | `"ready"` |
| `expected_revision` | integer | 是 | 不适用 | 写入时必须原样提交；新建为 1，每次成功 apply 后 +1 | `1` |
| `source_revision_id` | integer/null | 是 | 尚未绑定成稿版本 | 生成该草稿所用的个人画像 revision id | `88` |
| `prompt_version` | string/null | 是 | 尚未生成 | 提示词版本 | `"profile-card-summarize-v2"` |
| `schema_version` | string/null | 是 | 尚未生成 | 草稿 schema 版本 | `"profile-card-summarize-v1"` |
| `fields` | object | 是 | 不适用 | 五个开放文本槽，见下表 |  |
| `fields.self_intro` | object | 是 | 不适用 | 自我介绍候选 |  |
| `fields.qa_1_partner` | object | 是 | 不适用 | 问答 1「理想的另一半」候选 |  |
| `fields.qa_3_love` | object | 是 | 不适用 | 问答 3「期待的爱情」候选 |  |
| `fields.qa_2_sports_candidates` | object | 是 | 不适用 | 问答 2「喜欢的运动」候选，文本在 `candidates` |  |
| `fields.interest_tag_candidates` | object | 是 | 不适用 | 兴趣标签候选，文本在 `candidates` |  |
| `fields.*.value` | string | 是 | 空串表示这一槽没有单值文案 | 模型给出的单段文本，最长按生成侧裁剪 | `"周末喜欢徒步。"` |
| `fields.*.candidates` | string[] | 是 | 空数组表示没有候选 | 只保留目录内标签；运动与兴趣槽使用它 | `["徒步"]` |
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
      "candidates": ["徒步"],
      "confidence": 0.7,
      "source_ref": null
    },
    "interest_tag_candidates": {
      "value": "",
      "candidates": ["徒步"],
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
- 读取范围是本人最新一条状态属于 `queued`、`running`、`ready`、`partial`、`applied` 的草稿。`discarded` 不返回；没有任何可读行时 `404 PROFILE_CARD_DRAFT_NOT_FOUND`。
- 幂等：只读，无 Idempotency-Key，不改状态、不占额度。
- 边界：不返回他人草稿。功能关闭时不回退成「返回旧草稿」。

**错误**

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 401 | （鉴权失败，非业务码） | 未登录或 token 无效 | 重新登录 |
| 403 | `AI_CONSENT_REQUIRED` | 未授权或已撤回 | 重新授权后再读 |
| 404 | `PROFILE_CARD_DRAFT_NOT_FOUND` | 本人没有未丢弃草稿 | 先调用 summarize |
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
| `expected_revision` | body | integer | 是 | 无 | `>= 0`，必须等于草稿当前 `expected_revision` | 乐观锁 |
| `accepted` | body | object | 否 | 空对象 | 禁止额外字段 | 用户确认要写入的内容 |
| `accepted.self_intro` | body | string/null | 否 | `null` | 最长 500；服务端再 strip | 要写入的自我介绍 |
| `accepted.qa_answers` | body | array/null | 否 | `null` | 每项见下表 | 要写入的「关于我」问答 |
| `accepted.qa_answers[].question_id` | body | integer | 项内必填 | 无 | 只能是 `1`、`2`、`3` | 1 理想的另一半；2 喜欢的运动；3 期待的爱情 |
| `accepted.qa_answers[].question` | body | string/null | 否 | 按 question_id 填充 | 最长 64 | 题干；空则服务端填固定题干 |
| `accepted.qa_answers[].answer` | body | string | 否 | `""` | 最长 300；写入前 strip，空答案跳过 | 用户确认后的答案 |
| `accepted.personal_tags` | body | string[]/null | 否 | `null` | 数组最长 10 | 要并入的兴趣标签；不在标签目录中的项被丢弃 |
| `rejected` | body | string[] | 否 | `[]` | 无格式枚举 | 用户明确不写的槽。只有事实列键会计入 `skipped_fields`：`height`、`education`、`income`、`occupation`、`city`、`education_level`、`city_code`、`weight`、`birthday`、`age` |
| `replace_existing` | body | object | 否 | `{"self_intro": false}` | 禁止额外字段 | 是否覆盖资料里已有内容 |
| `replace_existing.self_intro` | body | boolean | 否 | `false` | 布尔 | `false` 且资料已有非空自我介绍时跳过，不覆盖 |

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
  "accepted": {
    "self_intro": "周末喜欢徒步和看展，希望找一个能一起出门的人。",
    "qa_answers": [
      {"question_id": 1, "answer": "希望对方也喜欢出门。"}
    ],
    "personal_tags": ["徒步"]
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
| `written_fields` | string[] | 是 | 空数组表示这次没有新写入 | 实际写入的槽：`self_intro`、`qa_1`/`qa_2`/`qa_3`、`personal_tags` | `["self_intro","qa_1"]` |
| `skipped_fields` | string[] | 是 | 空数组表示没有跳过 | 因已有内容、空文本或被拒绝的事实列而没写的槽 | `["height"]` |
| `profile` | object/null | 是 | 无 | 写入后的个人资料；无资料变更时为写入前的当前资料。结构同 `GET /users/me/profile` | 见个人资料文档 |

**返回示例**

```json
{
  "status": "applied",
  "replayed": false,
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

- 前置条件：登录、画像功能开启、`profile_text_extract` 有效，且本人最新可读草稿状态是 `ready`、`partial` 或 `applied`。`queued`/`running`/`failed` 返回 `400`，文案「当前草稿不可写入资料卡」。没有可读草稿返回 `404`。
- 调用顺序：先 GET draft，把返回的 `expected_revision` 原样带回。用户必须在客户端改过或确认过文本后再提交；服务端仍会做内容审核。
- 幂等：草稿已是 `applied`，且 Idempotency-Key 与请求摘要都和上次相同，回放第一次响应，不再写资料、不再把 `expected_revision` +1。同一 key 但请求摘要不同不会回放；此时状态仍是 `applied`，只要 `expected_revision` 与当前值一致，会再次写入并把 revision 再 +1。状态不是 `ready`、`partial`、`applied` 时返回 400。
- 乐观锁：`expected_revision` 不一致返回 `409 DRAFT_VERSION_CONFLICT`。成功写入后该值 +1。
- 自我介绍：空串跳过。资料里已有非空介绍且 `replace_existing.self_intro=false` 时跳过，不覆盖。`true` 才替换。
- 问答：`question_id` 1 和 3 在资料里已有非空答案时跳过。`question_id` 2 允许覆盖已有答案。空答案不写。答案写入前截断到 300 字。
- 标签：只保留标签目录内的值，与现有标签去重合并，总数不超过 10。本次即使没有新增目录内标签，只要请求带了 `personal_tags`（含空数组），仍会计入 `written_fields`。
- 事实列（身高、学历、收入、职业、城市等）永远不会从本接口写入。把它们放进 `rejected` 只会出现在 `skipped_fields`。
- 内容审核拒绝自我介绍或问答时，整个请求失败为 `400 AI_INPUT_INVALID`，不把草稿标成 `applied`。审核替换时使用替换后的展示文本。
- 事务：有资料变更时，个人资料由 `update_profile` 先提交，路由再提交草稿的 `applied` 标记，这两步不是同一个数据库事务。若后一步失败，资料可能已经写入，而草稿仍不是 `applied`，同一 key 不会回放。没有资料变更时，路由只提交草稿标记。任一步在返回前失败都不返回 200。
- 额度：本接口不占 summarize 的每日 5 次额度。
- 边界：不能写他人草稿。功能关闭时不能借此接口清理或补写。重复提交同一已应用请求应看 `replayed`，不要据此再改 UI 草稿。

**错误**

| HTTP | 错误码 | 触发条件 | 前端处理建议 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | key 非法；草稿还不能写；自我介绍或问答被审核拒绝 | 刷新草稿或改文案；不要原样重试被拒内容 |
| 401 | （鉴权失败，非业务码） | 未登录或 token 无效 | 重新登录 |
| 403 | `AI_CONSENT_REQUIRED` | 授权已撤回 | 重新授权 |
| 404 | `PROFILE_CARD_DRAFT_NOT_FOUND` | 没有可读草稿 | 重新 summarize |
| 409 | `DRAFT_VERSION_CONFLICT` | `expected_revision` 与服务端不一致 | 重新 GET draft 后再提交 |
| 422 | （请求体或资料校验失败） | revision 缺失/为负、问答 question_id 非法、`accepted` 含未声明字段；或个人资料更新本身拒绝该内容 | 按字段表修正 |
| 503 | `AI_FEATURE_DISABLED` | 画像功能关闭 | 停止写入 |
| 503 | `AI_TEMPORARILY_UNAVAILABLE` | 写入过程出现未分类异常，`retryable=true` | 先 GET draft 看是否已是 `applied`，再决定是否用同一 key 重试 |

非法 body 示例：

```json
{
  "expected_revision": 1,
  "accepted": {"height": 180}
}
```

`accepted` 不允许 `height`，返回 422。身高只能出现在 `rejected`，并且仍然不会被写入。
