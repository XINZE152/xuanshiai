# AI 分身接口

## 1. 统一约定

- 基础路径：`/api/v1/ai-avatars`
- 认证：全部接口要求 `Authorization: Bearer <access_token>`。
- 数据格式：请求和响应均为 `application/json`。
- 隐私：公开资料上下文由后端按目标用户当前隐私设置生成。客户端不得提交或覆盖资料上下文。
- 隔离：会话保存在 `ai_avatar_conversation`、`ai_avatar_message`，不进入真人消息列表、未读数或通知。
- 模型：后端调用 OpenAI 兼容的 `/chat/completions`，不会向客户端返回供应商密钥。
- 限额：发送消息默认每位用户每日 20 次，以 UTC 自然日重置，实际值由 `AI_DAILY_LIMIT` 配置。
- 转交：当前真实接口不转交真人，`handoffRequired=false`、`handoffStatus=not_requested`。

### 1.1 公共消息字段

| 字段 | 类型 | 必返 | 空值 | 含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `id` | integer | 是 | 否 | 消息 ID；欢迎消息固定为 `0` | `12` |
| `type` | string | 是 | 否 | 固定为 `text` | `text` |
| `content` | string | 是 | 否 | 用户问题、AI 回答或欢迎语 | `Ta 喜欢徒步` |
| `time` | integer | 是 | 否 | Unix 毫秒时间戳 | `1786675200000` |
| `showTime` | boolean | 是 | 否 | 前端是否显示独立时间标签 | `false` |
| `isMine` | boolean | 是 | 否 | 是否为当前访问者发送 | `false` |
| `avatar` | string/null | 是 | 可空 | AI 消息头像；用户消息可空 | `/storage/uploads/a.webp` |
| `source` | string | 是 | 否 | `user`、`real-ai` 或 `system` | `real-ai` |
| `category` | string | 是 | 否 | `basic`、`interest`、`expectation`、`platform`、`general` | `interest` |
| `handoffRequired` | boolean | 是 | 否 | 当前固定 `false` | `false` |
| `handoffStatus` | string | 是 | 否 | 当前固定 `not_requested` | `not_requested` |

### 1.2 公共错误

| HTTP | 触发条件 | 响应示例 | 前端处理 |
| --- | --- | --- | --- |
| `401` | Token 缺失、过期或会话失效 | `{"detail":"请先登录"}` | 进入登录流程 |
| `403` | 自己的分身、双方任一方拉黑、资料不可见、认证或会员条件不满足 | `{"detail":"该用户当前未公开个人资料"}` | 关闭入口并提示原因 |
| `404` | 目标用户不存在或账号不可用 | `{"detail":"用户不存在"}` | 返回上一页并刷新用户列表 |
| `422` | 路径或请求体校验失败、问题命中拒绝规则 | `{"detail":[{"msg":"String should have at most 300 characters"}]}` | 保留输入并提示修改 |
| `429` | 当日 AI 提问达到限额 | `{"detail":"今日 AI 分身提问已达 20 次上限"}` | 禁止重复提交，次日重试 |
| `503` | AI 未配置、供应商异常或 Redis 在生产环境不可用 | `{"detail":"AI 服务暂时不可用，请稍后重试"}` | 展示重试，不回退 Mock |
| `504` | AI 供应商请求超时 | `{"detail":"AI 回答超时，请稍后重试"}` | 保留输入并允许重试 |

## 2. 读取 AI 分身公开资料

**基本信息**：读取当前访问者有权看见的资料快照。URL `GET /api/v1/ai-avatars/{target_user_id}/profile`；需登录；成功状态 `200`。

**请求参数**：

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 | 合法/非法示例 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `target_user_id` | path | integer | 是 | 无 | `>=1`，且不能等于当前用户 | AI 分身所属用户 ID | `2` / `0` |
| `Authorization` | header | string | 是 | 无 | Bearer Token | 当前访问者身份 | `Bearer ey...` / 缺失 |

**请求体示例**：无请求体。

```http
GET /api/v1/ai-avatars/2/profile HTTP/1.1
Authorization: Bearer <access-token>
```

**返回参数**：

| 字段 | 类型 | 必返 | 空值 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `id` | integer | 是 | 否 | 目标用户 ID | `2` |
| `name` | string | 是 | 否 | 公开昵称，缺失时为 `Ta` | `林夏` |
| `avatar` | string/null | 是 | 可空 | 公开头像 URL | `/storage/uploads/2.webp` |
| `age` | integer/null | 是 | 可空 | 由生日计算的年龄 | `28` |
| `city` | string/null | 是 | 可空 | 公开城市或城市编码 | `南京` |
| `job` | string/null | 是 | 可空 | 未隐藏且有权限查看的职业 | `产品经理` |
| `education` | string/null | 是 | 可空 | 未隐藏且有权限查看的学历标签 | `本科` |
| `tags` | string[] | 是 | 空数组 | 公开兴趣、性格和资料标签 | `["徒步"]` |
| `bio` | string/null | 是 | 可空 | 公开自我介绍 | `喜欢自然和阅读` |
| `interests` | string[] | 是 | 空数组 | 公开兴趣摘要 | `["徒步","摄影"]` |
| `expectations` | string[] | 是 | 空数组 | 当前访问者有权查看的择偶期待 | `["认真稳定"]` |
| `allowExpectations` | boolean | 是 | 否 | 是否允许展示择偶期待 | `true` |
| `restricted` | boolean | 是 | 否 | 详细资料是否因会员隐私受限 | `false` |
| `aiMode` | string | 是 | 否 | 固定为 `real` | `real` |

**返回示例**：

```json
{"id":2,"name":"林夏","avatar":null,"age":28,"city":"南京","job":"产品经理","education":"本科","tags":["徒步"],"bio":"喜欢自然和阅读","interests":["徒步","摄影"],"expectations":["认真稳定"],"allowExpectations":true,"restricted":false,"aiMode":"real"}
```

**使用方法与业务规则**：进入聊天页前调用。后端检查账号状态、拉黑关系、`show_profile`、`who_can_see_me`、`match_status`、会员与实名认证可见条件。本接口不扣普通主页浏览次数。隐私变化立即生效，旧客户端不得使用本地资料替代失败响应。

**错误**：见 1.2。`403` 时不得继续调用消息接口。接口幂等，不创建会话，不产生通知。

**兼容性**：新增接口，不影响 `/users/{id}/profile`。响应新增字段时旧客户端应忽略未知字段。

## 3. 读取 AI 分身聊天记录

**基本信息**：读取当前用户与指定分身的独立历史。URL `GET /api/v1/ai-avatars/{target_user_id}/conversations`；需登录；成功状态 `200`。

**请求参数**：`target_user_id` 与 `Authorization` 的类型、校验和含义同第 2 节。非法示例为 `target_user_id=-1`。无 query，无请求体。

```http
GET /api/v1/ai-avatars/2/conversations HTTP/1.1
Authorization: Bearer <access-token>
```

**返回参数**：

| 字段 | 类型 | 必返 | 空值 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `targetUserId` | integer | 是 | 否 | 目标用户 ID | `2` |
| `messages` | object[] | 是 | 至少含欢迎消息 | 独立 AI 消息数组；内部字段见 1.1 | `[{"id":0,...}]` |

**返回示例**：

```json
{"targetUserId":2,"messages":[{"id":0,"type":"text","content":"你好，我是林夏的 AI 分身。这里只参考 Ta 当前对你公开的资料，不是真人聊天，Ta 也不会收到提醒。你可以问我基本资料、兴趣爱好、择偶标准或平台规则。","time":1786675200000,"showTime":false,"isMine":false,"avatar":null,"source":"system","category":"general","handoffRequired":false,"handoffStatus":"not_requested"}]}
```

**使用方法与业务规则**：成功加载公开资料后调用。没有数据库历史时仍返回一条临时欢迎消息；欢迎消息 ID 为 `0`，不会存入数据库。最多返回当前会话前 200 条数据库消息。历史只对发起者可见，不产生已读或未读状态。

**错误**：见 1.2。接口幂等，不扣 AI 提问额度。

**兼容性**：新增接口；AI 历史不会出现在任何真人会话接口中。

## 4. 向 AI 分身发送问题

**基本信息**：调用真实模型并在成功后保存问题与回答。URL `POST /api/v1/ai-avatars/{target_user_id}/messages`；需登录；`Content-Type: application/json`；成功状态 `200`。

**请求参数**：

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 | 合法/非法示例 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `target_user_id` | path | integer | 是 | 无 | `>=1`，不能为本人 | 分身所属用户 ID | `2` / `0` |
| `Authorization` | header | string | 是 | 无 | Bearer Token | 当前访问者身份 | `Bearer ey...` / 缺失 |
| `content` | body | string | 是 | 无 | 去首尾和重复空白后 1-300 字符 | 用户问题 | `Ta 喜欢什么？` / 301 字符 |

**请求体示例**：

```http
POST /api/v1/ai-avatars/2/messages HTTP/1.1
Authorization: Bearer <access-token>
Content-Type: application/json

{"content":"Ta 喜欢什么？"}
```

非法请求体：`{"content":""}`。

**返回参数**：

| 字段 | 类型 | 必返 | 空值 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `messages` | object[] | 是 | 否 | 保存后的完整 AI 会话；内部字段见 1.1 | `[{"id":0,...}]` |
| `result.reply` | string | 是 | 否 | 本次模型回答 | `Ta 的公开资料提到喜欢徒步。` |
| `result.category` | string | 是 | 否 | 本次问题分类，枚举见 1.1 | `interest` |
| `result.source` | string | 是 | 否 | 固定 `real-ai` | `real-ai` |
| `result.handoffRequired` | boolean | 是 | 否 | 当前固定 `false` | `false` |
| `result.handoffStatus` | string | 是 | 否 | 当前固定 `not_requested` | `not_requested` |

**返回示例**：

```json
{"messages":[{"id":0,"type":"text","content":"欢迎语","time":1786675200000,"showTime":false,"isMine":false,"avatar":null,"source":"system","category":"general","handoffRequired":false,"handoffStatus":"not_requested"},{"id":11,"type":"text","content":"Ta 喜欢什么？","time":1786675201000,"showTime":false,"isMine":true,"avatar":null,"source":"user","category":"interest","handoffRequired":false,"handoffStatus":"not_requested"},{"id":12,"type":"text","content":"Ta 的公开资料提到喜欢徒步，这是 AI 回答。","time":1786675202000,"showTime":false,"isMine":false,"avatar":null,"source":"real-ai","category":"interest","handoffRequired":false,"handoffStatus":"not_requested"}],"result":{"reply":"Ta 的公开资料提到喜欢徒步，这是 AI 回答。","category":"interest","source":"real-ai","handoffRequired":false,"handoffStatus":"not_requested"}}
```

**使用方法与业务规则**：先调用资料和历史接口。服务端重新检查隐私，读取最近 `AI_MAX_CONTEXT_MESSAGES` 条历史，调用供应商，过滤输出后再一次性保存用户问题和 AI 回答。供应商或数据库失败时不保存本轮消息并返还本次额度。不要自动重试 POST；由用户主动重试，避免产生两次回答。

**频率与并发**：每位访问者共享每日额度，不按目标分别计算。并发请求分别计数；数据库会话按访问者与目标唯一。当前无 `Idempotency-Key`，客户端发送期间必须禁用重复提交。

**错误**：除 1.2 外，AI 未配置返回 `503`；超时返回 `504`；额度不足返回 `429`。错误响应不会包含供应商响应体、密钥或内部 Prompt。

**兼容性**：新增接口。当前为非流式响应，未来增加流式接口时保留本接口。

## 5. 清空 AI 分身聊天记录

**基本信息**：删除当前访问者与目标分身的独立会话。URL `DELETE /api/v1/ai-avatars/{target_user_id}/conversations`；需登录；成功状态 `200`。

**请求参数**：`target_user_id` 与 `Authorization` 同第 2 节；无 query，无请求体。非法示例：`target_user_id=0`。

```http
DELETE /api/v1/ai-avatars/2/conversations HTTP/1.1
Authorization: Bearer <access-token>
```

**返回参数**：

| 字段 | 类型 | 必返 | 空值 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `targetUserId` | integer | 是 | 否 | 被清空会话的目标用户 ID | `2` |
| `deleted` | boolean | 是 | 否 | 删除操作已完成；无历史时也为 `true` | `true` |

**返回示例**：`{"targetUserId":2,"deleted":true}`。

**使用方法与业务规则**：用户确认后调用。删除会话会级联删除其 AI 消息；不会删除真人消息、通知或目标用户资料。重复调用结果相同，不扣 AI 额度。删除后重新读取历史只返回欢迎消息。

**错误**：见 1.2。只有当前访问者自己的会话可被删除，不接受会话 ID，避免越权删除。

**兼容性**：新增接口，无旧数据迁移；原前端本地 Mock 历史保留在独立命名空间，不会上传到后端。

## 6. 本地与部署配置

在未提交的 `.env` 中配置：

```env
AI_PROVIDER=openai_compatible
AI_BASE_URL=https://供应商地址/v1
AI_API_KEY=实际密钥
AI_MODEL=实际模型名
AI_TIMEOUT_SECONDS=20
AI_MAX_OUTPUT_TOKENS=500
AI_MAX_CONTEXT_MESSAGES=12
AI_DAILY_LIMIT=20
```

本地无密钥的兼容服务可不设置 `AI_API_KEY`。staging/production 强制 `AI_BASE_URL` 使用 HTTPS。修改配置后需要重启 FastAPI。
