# AI 能力与 OpenAPI 索引

接口前缀：`/api/v1/ai`。本文件提供早期文字 AI 接口的入口说明和当前运行时路径索引；画像、搜索、授权、任务、匹配度、记忆、资料卡、军师、分身和语音的字段级契约分别见同目录专门文档。

## 生产开关与配置

AI **默认关闭**。生产环境不得仅通过设置一个总开关启用；必须同时满足对应功能开关、`ai_policy_approved`、`ai_provider_approved`、`ai_retention_policy_version`、真实 provider 和凭据校验，否则启动或运行时 fail closed（稳定返回 `503 AI_FEATURE_DISABLED`）。未完成真实 MySQL/Redis/Provider 验收前，不得生成生产启用证据。

实际配置字段使用 Pydantic Settings 的 `AI_*` 环境变量映射：

```env
ENVIRONMENT=development
AI_MASTER_ENABLED=false
AI_PROFILE_ENABLED=false
AI_SEARCH_ENABLED=false
AI_COMPATIBILITY_SHADOW_ENABLED=false
AI_RECOMMEND_ENABLED=false
AI_MOXIANG_JOURNEY_ENABLED=false
AI_VOICE_ENABLED=false
AI_PROVIDER=mock
AI_TEST_ONLY_USER_IDS=
```

### 测试期用户隔离（`AI_TEST_ONLY_USER_IDS`）

临时接入第三方（含免费）端点做联调时，用此变量把 AI 能力**限制在指定用户**，避免真实用户数据被发送到第三方。

- 格式：逗号分隔的 `user_id`，容忍空格、全角逗号 `，`、中英文分号；非数字片段被忽略。例如 `AI_TEST_ONLY_USER_IDS=21,22,23,24`。
- 为空（默认）：不做任何限制，行为与历史一致。
- 非空：只有列表内的用户能通过 AI 门禁；**列表外的用户、以及没有请求上下文的调用（后台任务、脚本）一律 fail closed**，返回 `503 AI_FEATURE_DISABLED`。
- 该检查在 `require_ai_feature` 内、在功能开关与生产审批门禁**之后**执行，因此不会掩盖原有的开关行为。
- 生效方式：改环境变量后必须重启进程（`Settings` 为启动期加载）。
- 只影响 AI 接口；额度、支付、实名等其它接口不受影响。

> 变更记录 2026-10-23：新增 `AI_TEST_ONLY_USER_IDS` 测试期用户隔离。默认空值，**对既有部署无行为变化**；仅当显式配置后才收紧 AI 调用范围。协议与响应结构不变（复用既有 `503 AI_FEATURE_DISABLED`）。

生产启用前还必须显式配置批准项和对应真实 provider 的凭据，例如
`AI_POLICY_APPROVED=true`、`AI_PROVIDER_APPROVED=true`、
`AI_RETENTION_POLICY_VERSION=<approved-version>`；`AI_PROVIDER=mock` 不得作为生产
provider。Provider API key 只能放在环境变量或配置中心，不能提交到 Git，也不能暴露给客户端。

旧版 `AI_ENABLED`、`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 不是当前统一 AI 门禁字段，
不要用它们判断生产是否已启用。

## 权限、额度与数据边界

- 所有 AI HTTP 接口至少需要登录；会员、授权、主体归属、可见性和功能开关按各专门契约执行，不把“会员”作为所有 AI 路径的通用假设。
- 普通响应不返回 provider trace、密钥、原始 prompt/response 或不必要的用户原文；任务结果通过安全引用和明确的业务 payload 返回。
- 额度、幂等、撤权、删除传播和 retention 以各专门文档及运行时实现为准；离线 Mock 不能替代真实依赖验收。

## 早期文字接口

以下路径保留现有文字能力，仍需遵守上面的发布门禁和各路由自身的鉴权/额度规则。

### `POST /api/v1/ai/assistant/sessions`

创建 AI 助手会话。请求体 `{"title":"聊天建议"}`，`title` 可选，最长 80 字符。返回会话 ID、标题、消息数和时间。会话只能由所属用户访问。

### `GET /api/v1/ai/assistant/sessions`

查询本人 AI 会话。Query：`page`（1~1000，默认 1）、`page_size`（1~50，默认 20）。返回 `items/page/page_size/total/has_more`。

### `POST /api/v1/ai/assistant/sessions/{session_id}/messages`

请求体：`{"content":"帮我看看这段聊天是否用心"}`，1~4000 字符。AI 会读取当前用户作为发送方或接收方的全部文本聊天记录，最多取最近 80 条，仅限本人会话上下文；撤回消息不读取。返回 AI 回复消息。AI 只提供沟通建议，不做医疗、法律或高风险判断。

### `POST /api/v1/ai/profile/polish`

只润色用户提交的文字，不自动写回资料，也不得补造职业、收入、学历等事实。

请求体：`content` 1~2000 字符；`style` 为 `natural/warm/humorous/mature/concise`；`max_length` 50~2000，默认 300。返回 `original/polished/style/changed_points/changed/fallback`：
- `changed`：本次是否真的产生改写；AI 未启用时的 mock 回显固定为 `false`。
- `fallback`：`none` 表示正常模型；`mock_echo` 表示测试环境 mock 回退，前端应提示“当前未产生改写”。
用户确认后由原资料接口保存。

错误：门禁失败返回 `503 {"detail":{"code":"AI_FEATURE_DISABLED","message":"AI 功能暂不可用","retryable":false,"feature":"profile"}}`；模型连续两次输出非 JSON 返回 `503 AI_OUTPUT_INVALID`，本次额度已退还。旧客户端读 `detail` 字符串会看到 `[object Object]`，请改读 `detail.code` 或顶层 `message`。

### `GET|POST /api/v1/ai/profile/thoughtfulness`

AI 用心度评审（完整契约见 `docs/api/AI用心度.md`）。GET 返回最新评审；POST 触发分析。每日额度和错误处理以专门文档为准。

### `POST /api/v1/ai/search`

AI 自然语言搜索入口；草稿、快照、搜索建议和异步任务契约见 `docs/api/AI搜索.md`。

### `GET /api/v1/ai/matches/{match_type}`

旧匹配解释入口；`match_type` 为 `who_likes_me/i_like/material/soul`。新 shadow 资料合拍参考见 `docs/api/AI匹配度.md`。

### `GET /api/v1/ai/capabilities`

**基本信息**

- 用途：一次性返回「当前登录用户此刻哪些 AI 能力真的可用」，供前端按真实开关出态
- 完整 URL：`/api/v1/ai/capabilities`
- Method：`GET`
- 是否需要登录：是
- 所需角色/权限：任意登录用户
- Content-Type：无请求体；响应 `application/json`
- 成功状态码：`200 OK`

**返回参数**

| 字段 | 类型 | 必返 | 含义 |
|---|---|---|---|
| `master` | boolean | 是 | AI 总闸；为 `false` 时其余字段恒为 `false` |
| `profile` | boolean | 是 | AI 画像 / 用心度 / 资料卡是否可用 |
| `search` | boolean | 是 | AI 搜索与自然语言筛选是否可用 |
| `compatibility` | boolean | 是 | 资料合拍参考（首页「看看合拍度」）是否可用；**2026-11-07 新增** |
| `voice` | boolean | 是 | 语音播报 / 语音输入是否可用 |
| `advisor` | boolean | 是 | AI 军师是否可用 |
| `avatar` | boolean | 是 | AI 分身是否可用 |

返回示例（默认关闭）：

```json
{ "master": false, "profile": false, "search": false, "compatibility": false, "voice": false, "advisor": false, "avatar": false }
```

**业务规则**

1. 每个字段等价于「对应接口此刻会不会返回 `503 AI_FEATURE_DISABLED`」，判定直接复用
   `services.ai.flags.require_ai_feature`（含 master 总闸、功能开关、生产三道审批门、
   `AI_TEST_ONLY_USER_IDS` 范围），**不在前端复制开关逻辑**。
2. `master=false` 时其余字段一定为 `false`，前端只需判 `master` 即可决定是否展示 AI 入口组。
3. 前端建议缓存 5 分钟；能力开关变更不需要推送，重新拉取即可。

**错误**

| HTTP | 触发条件 | 前端处理 |
|---|---|---|
| 401 | 未登录 / token 失效 | 走登录引导 |

**兼容性**：新增接口，无旧调用方。

## 运行时 OpenAPI 路径对账（2026-09-25）

以下清单以当前应用 `GET /openapi.json` 的 `paths` 为准；参数名使用路由模板。新增或删除 AI HTTP 路径时必须同步本索引和对应专门文档。

- **Assistant / Advisor / Avatar**：`/ai/assistant/sessions`（GET/POST）、`/ai/assistant/sessions/{session_id}/messages`（POST）；`/ai/advisor/sessions`（GET/POST）、`/ai/advisor/sessions/{session_id}`（DELETE）、`/ai/advisor/sessions/{session_id}/advice`（POST）、`/ai/advisor/messages/{message_id}/feedback`（POST）；`/ai/avatar/{target_user_id}/reply`（POST）；`/ai-avatars/me/dashboard`（GET）、`/ai-avatars/me/questions`（POST）、`/ai-avatars/me/questions/{question_id}`（DELETE）、`/ai-avatars/me/questions/{question_id}/answer`（POST）、`/ai-avatars/me/answers/{answer_id}`（DELETE）、`/ai-avatars/{target_user_id}/profile`（GET）、`/ai-avatars/{target_user_id}/conversations`（GET/DELETE）、`/ai-avatars/{target_user_id}/messages`（POST）。
- **画像会话与发布**：`/ai/profile-sessions`（POST）、`/ai/profile-sessions/{session_id}`（GET/DELETE）、`/ai/profile-sessions/{session_id}/turns`（GET/POST）、`/ai/profile-sessions/{session_id}/mode`（POST）、`/ai/profile-sessions/{session_id}/pause`（POST）、`/ai/profile-sessions/{session_id}/resume`（POST）、`/ai/profile-sessions/{session_id}/skip-question`（POST）、`/ai/profile-sessions/update-intent`（POST）；`/ai/profile-drafts/{draft_id}`（GET/PATCH）、`/ai/profile-drafts/{draft_id}/preview`（POST）、`/ai/profile-drafts/{draft_id}/publish`（POST）、`/ai/profile-previews/{preview_id}`（GET）；`/ai/profile-revisions`（GET）、`/ai/profile-revisions/{revision_id}/restore`（POST）；`/ai/profiles/{subject}`（DELETE）、`/ai/profiles/{subject}/fields`（GET）、`/ai/profiles/{subject}/fields/{field_key}`（DELETE）、`/ai/profiles/{subject}/narrative`（GET）、`/ai/profiles/{subject}/narrative/confirm`（POST）、`/ai/profiles/{subject}/narrative/regenerate`（POST）。
- **资料卡 / 搜索 / 推荐**：`/ai/profile-card/draft`（GET）、`/ai/profile-card/summarize`（POST）、`/ai/profile-card/draft/apply`（POST）；`/ai/search-drafts`（POST）、`/ai/search-drafts/{draft_id}`（GET/PATCH）、`/ai/search-drafts/{draft_id}/confirm`（POST）、`/ai/search-snapshots/{snapshot_id}`（DELETE）、`/ai/search-snapshots/{snapshot_id}/results`（GET）、`/ai/search-suggestions`（GET）、`/ai/search-suggestions/generate`（POST）、`/ai/recommendations`（GET）。
- **授权 / 任务 / 匹配度 / 记忆 / 墨相师**：`/ai/consents`（GET）、`/ai/consents/{scope}`（PUT/DELETE）；`/ai/tasks/{task_id}`（GET）、`/ai/tasks/{task_id}/cancel`（POST）；`/ai/compatibility/{target_user_id}`（GET）、`/ai/compatibility/{target_user_id}/recompute`（POST）；`/ai/memory`、`/ai/memory/view`、`/ai/memory/{claim_id}/confirm`、`/ai/memory/{claim_id}/correct`、`/ai/memory/{claim_id}/suppress`、`/ai/memory/suppressions/{suppression_id}/lift`、`/ai/memory/grants/{grant_id}/revoke`、`/ai/memory/pause`、`/ai/memory/forget`；`/ai/moxiang/state`（GET）、`/ai/moxiang/archive`（GET）、`/ai/moxiang/journey/start`（POST）。
- **语音 HTTP**：`/voice/ws-ticket`（POST）、`/voice/transcribe`（POST）、`/voice/synthesize`（POST）。
- **能力探测**：`/ai/capabilities`（GET）。

WebSocket 路径不会出现在 OpenAPI `paths`，但属于同一发布边界：
`/voice/conversation?ticket=<ticket>` 和 `/voice/moxiang-master?ticket=<ticket>`。两者均先调用
`POST /api/v1/voice/ws-ticket`，ticket 有效期 60 秒且只能消费一次；协议细节见
`docs/api/语音.md` 和 `docs/api/墨相师实时整理WebSocket.md`。

### 变更记录

- 2026-11-07（AIT-07 / BE-01）：`GET /ai/capabilities` 响应**新增 `compatibility` 字段**。
  - 变更前：只下发 `master/profile/search/voice/advisor/avatar`，首页「看看合拍度」入口不受探测覆盖，用户点进去才收到 `503 AI_FEATURE_DISABLED`（`app/services/ai/flags.py` 的 `AiFeature.COMPATIBILITY_SHADOW` 门禁）。
  - 变更后：新增 `compatibility: boolean`，取值同样由 `require_ai_feature(COMPATIBILITY_SHADOW, settings)` 判定（master 总闸 + `AI_COMPATIBILITY_SHADOW_ENABLED` + 生产审批门 + `AI_TEST_ONLY_USER_IDS` 范围），与 `GET /ai/compatibility/{id}` 会不会 503 严格等价。
  - 影响范围：**响应字段新增，向后兼容**，URL/Method/认证/权限/既有字段语义均不变；旧客户端忽略该字段即可，行为与变更前一致。前端可用它在渲染前隐藏或标注合拍度入口。
- 2026-11-06（UT-2026-11-02-DOC-02，Q35/Q49/Q24）：①`POST /ai/profile/polish` 新增 `changed`、`fallback` 字段，用于前端识别 mock 回显“未产生改写”；②用心度/润色改为按 `AiFeature.PROFILE` 门禁，与 `GET /ai/capabilities.profile` 一致；③门禁失败统一返回 `AI_FEATURE_DISABLED`，模型连续两次非 JSON 返回 `AI_OUTPUT_INVALID`（`retryable=true`）；④`detail` 由字符串改为对象，旧客户端请改读 `detail.code` 或顶层 `message`。
- 2026-11-02：新增 `GET /ai/capabilities` 运行时能力探测。此前前端无法预知 AI 开关，四个症状（AI 搜索不可用 / 画像不可用 / 语音不可用 / 用心度 503）实为同一事实。
- 2026-09-25：按当前运行时 OpenAPI 重建路径索引；AI 默认关闭和生产 fail-closed 配置改为实际 `ai_*` 字段，移除旧的 `AI_ENABLED=true` 示例。
- 2026-09-07：新增 AI 资料用心度评审接口（GET/POST `/ai/profile/thoughtfulness`）。
- 2026-08-19：新增阶段一 AI 助手、文字资料润色、自然语言搜索和四类匹配解释接口。
- 2026-08-19：图片美化、海报图片增强、向量检索和非会员收费暂不实现。
