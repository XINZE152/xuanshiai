# AI 角色扮演接口契约

> 任务标识：`AI-REPORT-ROLEPLAY-20261109`
> 适用范围：微信小程序用户侧「角色扮演」独立面板。与知遇人设、小猫知遇 IP、`continuous_v2` 对话链路完全解耦；数据复用现有 `ai_profile_turn` 表，不新建表、不加列、不写迁移。

## 一、接口总览

| 接口 | Method | 完整 URL | 说明 |
| --- | --- | --- | --- |
| 角色预设 | GET | `/api/v1/ai/roleplay/scenes` | 返回服务端白名单角色（含独立名字、形象 Token、主动开场白） |
| 对话历史 | GET | `/api/v1/ai/roleplay/turns` | 本人指定角色的历史消息，按时间升序 |
| 提交一轮 | POST | `/api/v1/ai/roleplay/submit` | 生成 AI 角色回复并落库 |
| 兴趣确认 | POST | `/api/v1/ai/roleplay/interests/confirm` | 用户显式确认后，把现实兴趣接入现有画像候选链 |

全部接口需登录（`Authorization: Bearer <access_token>`），复用 `require_ai_feature(AiFeature.PROFILE, settings)`；未开放返回 `503 AI_FEATURE_DISABLED`。错误码复用冻结的 `AiErrorCode`，不新增。

---

## 二、GET /api/v1/ai/roleplay/scenes

**基本信息**：用途=读取角色预设；登录=是；权限=本人；Content-Type=`application/json`；成功状态码 `200`。

**请求参数**：无请求体、无 query 参数。

**请求示例**：

```http
GET /api/v1/ai/roleplay/scenes HTTP/1.1
Authorization: Bearer <access_token>
```

非法示例：`POST /api/v1/ai/roleplay/scenes` → `405 Method Not Allowed`。

**返回参数**：

| 字段 | 类型 | 必返 | 空值含义 | 业务含义 | 示例 |
| --- | --- | --- | --- | --- | --- |
| `success` | bool | 是 | — | 统一响应包装 | `true` |
| `data.scenes` | array | 是 | `[]`=暂无可用角色 | 角色预设列表 | 见下 |
| `data.scenes[].key` | string | 是 | — | 白名单枚举，提交时回传 | `coffee_chat` |
| `data.scenes[].role_name` | string | 是 | — | 独立角色名，不含「知遇」 | `林夏` |
| `data.scenes[].label` | string | 是 | — | 场景卡标题 | `咖啡馆闲聊` |
| `data.scenes[].description` | string | 是 | — | 场景一句话说明 | — |
| `data.scenes[].avatar_token` | string | 是 | — | `uni.scss` 已登记 Token 名，前端用 `var(--token)` 取色 | `--accent` |
| `data.scenes[].avatar_glyph` | string | 是 | — | 角色字形徽标，不复用小猫形象 | `夏` |
| `data.scenes[].opening_line` | string | 是 | `""`=无开场白 | 进入后角色主动向用户问话 | — |
| `data.notice` | string | 是 | — | AI 角色扮演标注 | — |
| `error` | object | 否 | 成功时省略 | 统一错误体 | — |

**返回示例**：

```json
{
  "success": true,
  "data": {
    "scenes": [
      {"key":"coffee_chat","role_name":"林夏","label":"咖啡馆闲聊","description":"在轻松的咖啡时光里聊聊近况与日常","avatar_token":"--accent","avatar_glyph":"夏","opening_line":"先别急着聊正事——今天这杯咖啡，你是特意给自己留了点时间，还是刚好路过？"}
    ],
    "notice": "AI 角色扮演，角色与对白均为虚构，不代表真实用户或平台意见。"
  }
}
```

空数据：`{"success":true,"data":{"scenes":[],"notice":"..."}}`。

---

## 三、GET /api/v1/ai/roleplay/turns

**基本信息**：登录=是；权限=本人；成功 `200`。

**请求参数表**：

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验 | 含义 | 合法示例 | 非法示例 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scene_key` | query | string | 是 | 无 | 必须在白名单，长度 1-64 | 角色 key | `coffee_chat` | `not_in_whitelist` |
| `limit` | query | int | 否 | `50` | `1..100` | 返回条数 | `50` | `0`、`101` |

**请求示例**：

```http
GET /api/v1/ai/roleplay/turns?scene_key=coffee_chat&limit=50 HTTP/1.1
Authorization: Bearer <access_token>
```

**返回参数表**：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `data.turns` | array | 是 | `[]`=尚未开始对话 | 按时间升序 |
| `data.turns[].turn_id` | string | 是 | — | 服务端稳定 ID |
| `data.turns[].role` | string | 是 | — | `user` / `assistant` |
| `data.turns[].answer_text` | string | 是 | — | 消息正文 |
| `data.turns[].created_at` | string\|null | 是 | `null`=旧行无时间 | ISO 8601 |

**返回示例**：`{"success":true,"data":{"turns":[]}}`。

---

## 四、POST /api/v1/ai/roleplay/submit

**基本信息**：登录=是；权限=本人；Content-Type=`application/json`；成功 `200`。

**请求参数表**：

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 | 合法示例 | 非法示例 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Idempotency-Key` | header | string | 是 | 8-128 位 ASCII | 幂等键，同键同请求回放原回复 | `roleplay-123456` | 空、`ab`、含中文 |
| `scene_key` | body | string | 是 | 白名单 | 角色 key | `coffee_chat` | `custom_role` |
| `user_message` | body | string | 是 | 1-2000 字，去空白后非空 | 用户本轮发言 | `我喜欢周末去爬山` | `""`、全空格 |
| `client_turn_id` | body | string | 是 | 8-128 字 | 客户端轮次 ID | `idem-key-01` | `""` |
| `custom_setting` | body | string\|null | 否 | ≤500 字，仅补充氛围 | 不改变角色身份与安全规则 | `想聊得慢一点` | 含联系方式、越权指令 |

**请求示例**：

```http
POST /api/v1/ai/roleplay/submit HTTP/1.1
Authorization: Bearer <access_token>
Idempotency-Key: roleplay-123456
Content-Type: application/json

{"scene_key":"coffee_chat","user_message":"我喜欢周末去爬山","client_turn_id":"roleplay-123456"}
```

非法示例：`{"scene_key":"not_in_whitelist","user_message":"你好","client_turn_id":"roleplay-123456"}` → `400 AI_INPUT_INVALID`。

**返回参数表**：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `data.session_id` | string | 是 | 独立命名空间 `roleplay:<user_id>:<scene_key>`，不登记到 `ai_profile_session` |
| `data.turn_id` | string | 是 | 本轮 AI 消息 ID |
| `data.assistant_reply` | string | 是 | AI 角色回复（≤4000 字） |
| `data.notice` | string | 是 | 「AI 角色扮演，角色与对白均为虚构」 |

**返回示例**：

```json
{"success":true,"data":{"session_id":"roleplay:10086:coffee_chat","turn_id":"<hex>","assistant_reply":"听起来爬山对你不只是运动……最近一次去是哪座山？","notice":"AI 角色扮演，角色与对白均为虚构，不代表真实用户或平台意见。"}}
```

**错误码表**：

| HTTP | 业务码 | 触发条件 | 前端处理 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | 幂等键/角色/消息非法 | 修正输入，不重试 |
| 409 | `TASK_IDEMPOTENCY_CONFLICT` | 同幂等键用于不同请求 | 换键重发 |
| 422 | `AI_POLICY_DENIED` | 内容或设定未通过安全校验 | 提示修改，不重试 |
| 422 | FastAPI 校验错误 | body 字段类型/长度不符 | 修正入参 |
| 503 | `AI_FEATURE_DISABLED` | AI 门禁未开放 | 入口置灰并说明 |
| 503 | `AI_TEMPORARILY_UNAVAILABLE` | Provider 失败 | 可重试，复用同一幂等键 |

**业务规则**：
- 前置条件：登录、AI 功能开放、角色在白名单。
- 调用顺序：先 `GET /scenes` 选角色（UI 直接展示 `opening_line` 主动问话），再 `POST /submit`。
- 幂等：同 `Idempotency-Key` + 同请求返回原回复；不同请求返回 409；重试必须复用同一键。
- 状态流转：每轮写 `ai_profile_turn` 的 user/assistant 两行，`source_type='ai_roleplay'`，事务内一次 `commit`。
- 边界：空历史、内容审核替换、Provider 空回复、并发重放。
- 兼容性：纯新增接口，不改 `continuous_v2` 三接口与响应形状。
- 隔离：独立 `session_id` 不写入 `ai_profile_session`，因此 `list_continuous_turns` / `_candidates` 的 `JOIN ai_profile_session` 天然读不到角色扮演消息。

---

## 五、POST /api/v1/ai/roleplay/interests/confirm

**基本信息**：登录=是；权限=本人；成功 `200`。这是角色扮演与用户画像的**唯一桥接**。

**请求参数表**：

| 参数 | 位置 | 类型 | 必填 | 校验 | 含义 | 合法示例 | 非法示例 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Idempotency-Key` | header | string | 是 | 8-128 位 ASCII | 幂等键 | `interest-123456` | 空 |
| `turn_id` | body | string | 是 | 必须属于本人角色扮演轮次 | 关联轮次 | `<turn_id>` | 他人/不存在的 ID |
| `tag` | body | string | 是 | 1-64 字，不含数字与联系方式关键词 | 用户本人真实兴趣 | `阅读` | `13800000000`、`加微信` |
| `confirm` | body | bool | 是 | 必须为 `true` | 显式确认 | `true` | `false` |

**请求示例**：

```http
POST /api/v1/ai/roleplay/interests/confirm HTTP/1.1
Authorization: Bearer <access_token>
Idempotency-Key: interest-123456
Content-Type: application/json

{"turn_id":"<hex>","tag":"阅读","confirm":true}
```

非法示例：`{"turn_id":"<hex>","tag":"13800000000","confirm":true}` → `400 AI_INPUT_INVALID`。

**返回参数表**：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `data.accepted` | bool | 是 | 是否已接入画像候选链 |
| `data.notice` | string | 是 | 明确告知仍需整份确认 |

**返回示例**：`{"success":true,"data":{"accepted":true,"notice":"已记录你确认的兴趣；仍需你在知遇中整份确认后才会进入正式画像。"}}`。

**错误码表**：

| HTTP | 业务码 | 触发条件 | 前端处理 |
| --- | --- | --- | --- |
| 400 | `AI_INPUT_INVALID` | 未确认 / 标签非法 / 轮次不属于本人 | 修正输入 |
| 409 | `AI_CONSENT_REQUIRED` | 无活动画像会话 | 提示先完成一次知遇授权对话 |
| 409 | `AI_INPUT_INVALID` | 画像会话状态变化 | 稍后重试 |
| 503 | `AI_FEATURE_DISABLED` | AI 门禁未开放 | 入口置灰 |

**业务规则**：不自动抽取、不写 `ai_profile_revision_field`、不产生 `<INTEREST>` 标记；确认后复用现有「落 turn → 抽取候选 → 用户整份确认 → 发布 revision」链路。虚构设定、角色台词、假设句一律不进入画像。

---

## 六、安全与隐私

- 角色预设为服务端白名单，`persona` 与安全规则不下发客户端；自定义设定经 `moderate_text` 审核且不能覆盖安全规则。
- 普通日志不写原始对话、原始 prompt、Provider 响应、密钥、手机号、精确地址。
- 响应不返回 `percent / score / affinity / heat / level` 等无出处指标，不返回用户 ID 以外的身份信息。
- AI 回复明确标注为 AI 角色扮演，不冒充真人，不承诺关系结果。
- 不新增数据库表、列、迁移、依赖或错误码。
