# AI 分身公开资料问答接口

更新时间：2026-09-07。接口前缀：`/api/v1/ai/avatar`。

本接口生成的是明确标注为 AI 的资料答复，不是本人聊天，不代表本人同意、意愿、
关系承诺或联系方式。它只使用记忆系统中的 `persona_context` 消毒投影；客户端不能
上传目标资料、授权、投影版本或聊天记录。

## 通用错误

| HTTP | 场景 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录或令牌失效 | 清空本地展示并跳转登录 |
| 404 | 目标不存在、资料不可见、已拉黑/注销、授权撤回或有效公开投影不存在 | 显示“公开资料暂时不可用”，不推断具体原因，也不回退读取本地快照 |
| 422 | `question` 为空、超过 300 字、含受限文本或未知字段；或模型回复触发冒充本人、关系承诺、联系方式、超长/敏感文本边界 | 让用户修改问题；不得自动重试模型输出 |
| 429 | 当日分身额度耗尽 | 显示次日恢复提示 |
| 503 | 记忆读取模式不是 `memory`、模型/Redis 暂不可用或模型 JSON 不合约 | 保留输入，允许用户主动重试；不得改用本地资料生成“真实分身”答复 |

---

#### 基于公开资料向 AI 分身提问

**基本信息**：`POST /api/v1/ai/avatar/{target_user_id}/reply`。需要 Bearer 登录；
无需会员作为额外门槛，目标资料当前可见性规则仍然生效。请求与响应
`Content-Type` 都为 `application/json`，成功状态码为 `201`。

**请求参数**：

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 | 合法示例 | 非法示例 |
| --- | --- | --- | ---: | --- | --- | --- | --- | --- |
| `target_user_id` | path | integer | 是 | 无 | 正整数；不能是当前用户；目标必须通过服务端可见性门 | 被询问的资料拥有者 | `42` | `0` / 本人 ID |
| `question` | body | string | 是 | 无 | 1–300 字符；内容过滤通过；仅普通文本 | 访客希望了解的公开资料问题 | `“Ta 平时喜欢什么？”` | 空字符串、301 字或 JSON 对象 |
| `Authorization` | header | string | 是 | 无 | `Bearer <token>` | 当前查看者身份 | `Bearer <token>` | 缺失 |

**请求体示例**：

```http
POST /api/v1/ai/avatar/42/reply
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "question": "Ta 平时喜欢什么？"
}
```

非法示例：

```json
{
  "question": "",
  "target_profile": {"age": 25}
}
```

返回 `422`；服务端不接受客户端代传资料，也不允许空问题。

**返回参数**：

| 字段 | 类型 | 必返 | 空值含义 | 枚举含义 | 业务含义 | 示例 |
| --- | --- | ---: | --- | --- | --- | --- |
| `target_user_id` | integer | 是 | 无 | 正整数 | 实际资料拥有者 ID | `42` |
| `reply` | string | 是 | 无 | 最多 600 字 | AI 根据获授权公开资料生成的答复 | `“我是 AI 分身，公开资料显示…”` |
| `ai_generated` | boolean | 是 | 恒为 `true` | `true`=AI 输出 | 让客户端稳定标记非真人消息 | `true` |
| `source` | string | 是 | 无 | 固定 `authorized_public_profile` | 答复数据来源类别，不包含真实字段内容 | `authorized_public_profile` |
| `disclaimer` | string | 是 | 无 | 无 | 不代表本人同意或承诺的声明 | `“这是 AI 分身…”` |

**返回示例**：

```json
{
  "target_user_id": 42,
  "reply": "我是 AI 分身。根据当前获授权的公开资料，Ta 分享了几项兴趣；未公开的信息建议在双方同意认识后再慢慢了解。",
  "ai_generated": true,
  "source": "authorized_public_profile",
  "disclaimer": "这是 AI 分身基于当前获授权的公开资料生成的回答，不代表本人同意、承诺或真实聊天。"
}
```

**使用方法与业务规则**：

- 前置条件：`AI_MEMORY_PROJECTION_READ_MODE=memory`；目标用户的
  `profile_text_extract` 授权、`persona_context/session_context/public_profile_summary`
  grant 和 active Projection 都有效；服务端同时复核目标当前资料可见性、拉黑、
  注销、投影策略和 privacy revision。
- 调用顺序：先由正常资料页完成可见性检查，再请求本接口；客户端的检查只是体验优化，
  服务端检查才是准入依据。进入 `404`/`503` 后不应读取旧快照或调用本地规则伪造答复。
- 使用边界：只含公开 allowlist 字段；不会包含目标的理想伴侣偏好、私密自由文本、
  原始事件、来源摘录、完整聊天、未审核媒体或联系方式。资料字段按不可信数据处理，
  不能指示模型改变规则。
- 输出边界：Provider 回复同样是不可信输入。服务端会在返回前拒绝冒充本人、代替
  本人作出关系同意/意愿/承诺、联系方式/住址、超过 600 字或命中内容过滤规则的文本；
  拒绝返回 422 并归还本次额度，客户端不得用本地资料代答。
- 限流：按查看者 UTC 日计数，默认 `AI_DAILY_AVATAR_LIMIT=20`；模型、格式或保存
  前错误会归还本次额度。接口无写入业务记录，因此不需要 `Idempotency-Key`。
- 状态与并发：答复无状态；授权/可见性任一变化会使后续请求重新 fail closed。缓存键
  含查看者、目标、投影版本和 privacy revision；当前授权会在使用缓存前复验。
- 兼容性：这是新增接口。微信小程序 `USE_MOCK=true` 保留本地演示；`USE_MOCK=false`
  才调用本接口。旧客户端不受影响，新客户端不得把本地演示回答标成后端真实答复。

**错误响应示例**：

```json
{
  "detail": "AI分身公开资料暂时不可用"
}
```
