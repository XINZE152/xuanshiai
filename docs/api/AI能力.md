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
```

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

请求体：`content` 1~2000 字符；`style` 为 `natural/warm/humorous/mature/concise`；`max_length` 50~2000，默认 300。返回 `original/polished/style/changed_points`。用户确认后由原资料接口保存。

### `GET|POST /api/v1/ai/profile/thoughtfulness`

AI 用心度评审（完整契约见 `docs/api/AI用心度.md`）。GET 返回最新评审；POST 触发分析。每日额度和错误处理以专门文档为准。

### `POST /api/v1/ai/search`

AI 自然语言搜索入口；草稿、快照、搜索建议和异步任务契约见 `docs/api/AI搜索.md`。

### `GET /api/v1/ai/matches/{match_type}`

旧匹配解释入口；`match_type` 为 `who_likes_me/i_like/material/soul`。新 shadow 资料合拍参考见 `docs/api/AI匹配度.md`。

## 运行时 OpenAPI 路径对账（2026-09-25）

以下清单以当前应用 `GET /openapi.json` 的 `paths` 为准；参数名使用路由模板。新增或删除 AI HTTP 路径时必须同步本索引和对应专门文档。

- **Assistant / Advisor / Avatar**：`/ai/assistant/sessions`（GET/POST）、`/ai/assistant/sessions/{session_id}/messages`（POST）；`/ai/advisor/sessions`（GET/POST）、`/ai/advisor/sessions/{session_id}`（DELETE）、`/ai/advisor/sessions/{session_id}/advice`（POST）、`/ai/advisor/messages/{message_id}/feedback`（POST）；`/ai/avatar/{target_user_id}/reply`（POST）；`/ai-avatars/me/dashboard`（GET）、`/ai-avatars/me/questions`（POST）、`/ai-avatars/me/questions/{question_id}`（DELETE）、`/ai-avatars/me/questions/{question_id}/answer`（POST）、`/ai-avatars/me/answers/{answer_id}`（DELETE）、`/ai-avatars/{target_user_id}/profile`（GET）、`/ai-avatars/{target_user_id}/conversations`（GET/DELETE）、`/ai-avatars/{target_user_id}/messages`（POST）。
- **画像会话与发布**：`/ai/profile-sessions`（POST）、`/ai/profile-sessions/{session_id}`（GET/DELETE）、`/ai/profile-sessions/{session_id}/turns`（GET/POST）、`/ai/profile-sessions/{session_id}/mode`（POST）、`/ai/profile-sessions/{session_id}/pause`（POST）、`/ai/profile-sessions/{session_id}/resume`（POST）、`/ai/profile-sessions/{session_id}/skip-question`（POST）、`/ai/profile-sessions/update-intent`（POST）；`/ai/profile-drafts/{draft_id}`（GET/PATCH）、`/ai/profile-drafts/{draft_id}/preview`（POST）、`/ai/profile-drafts/{draft_id}/publish`（POST）、`/ai/profile-previews/{preview_id}`（GET）；`/ai/profile-revisions`（GET）、`/ai/profile-revisions/{revision_id}/restore`（POST）；`/ai/profiles/{subject}`（DELETE）、`/ai/profiles/{subject}/fields`（GET）、`/ai/profiles/{subject}/fields/{field_key}`（DELETE）、`/ai/profiles/{subject}/narrative`（GET）、`/ai/profiles/{subject}/narrative/confirm`（POST）、`/ai/profiles/{subject}/narrative/regenerate`（POST）。
- **资料卡 / 搜索 / 推荐**：`/ai/profile-card/draft`（GET）、`/ai/profile-card/summarize`（POST）、`/ai/profile-card/draft/apply`（POST）；`/ai/search-drafts`（POST）、`/ai/search-drafts/{draft_id}`（GET/PATCH）、`/ai/search-drafts/{draft_id}/confirm`（POST）、`/ai/search-snapshots/{snapshot_id}`（DELETE）、`/ai/search-snapshots/{snapshot_id}/results`（GET）、`/ai/search-suggestions`（GET）、`/ai/search-suggestions/generate`（POST）、`/ai/recommendations`（GET）。
- **授权 / 任务 / 匹配度 / 记忆 / 墨相师**：`/ai/consents`（GET）、`/ai/consents/{scope}`（PUT/DELETE）；`/ai/tasks/{task_id}`（GET）、`/ai/tasks/{task_id}/cancel`（POST）；`/ai/compatibility/{target_user_id}`（GET）、`/ai/compatibility/{target_user_id}/recompute`（POST）；`/ai/memory`、`/ai/memory/view`、`/ai/memory/{claim_id}/confirm`、`/ai/memory/{claim_id}/correct`、`/ai/memory/{claim_id}/suppress`、`/ai/memory/suppressions/{suppression_id}/lift`、`/ai/memory/grants/{grant_id}/revoke`、`/ai/memory/pause`、`/ai/memory/forget`；`/ai/moxiang/state`（GET）、`/ai/moxiang/archive`（GET）、`/ai/moxiang/journey/start`（POST）。
- **语音 HTTP**：`/voice/ws-ticket`（POST）、`/voice/transcribe`（POST）、`/voice/synthesize`（POST）。

WebSocket 路径不会出现在 OpenAPI `paths`，但属于同一发布边界：
`/voice/conversation?ticket=<ticket>` 和 `/voice/moxiang-master?ticket=<ticket>`。两者均先调用
`POST /api/v1/voice/ws-ticket`，ticket 有效期 60 秒且只能消费一次；协议细节见
`docs/api/语音.md` 和 `docs/api/墨相师实时整理WebSocket.md`。

### 变更记录

- 2026-09-25：按当前运行时 OpenAPI 重建路径索引；AI 默认关闭和生产 fail-closed 配置改为实际 `ai_*` 字段，移除旧的 `AI_ENABLED=true` 示例。
- 2026-09-07：新增 AI 资料用心度评审接口（GET/POST `/ai/profile/thoughtfulness`）。
- 2026-08-19：新增阶段一 AI 助手、文字资料润色、自然语言搜索和四类匹配解释接口。
- 2026-08-19：图片美化、海报图片增强、向量检索和非会员收费暂不实现。
