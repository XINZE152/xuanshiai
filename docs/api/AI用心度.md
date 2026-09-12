# AI 用心度评审

> 接口前缀：`/api/v1/ai/profile/thoughtfulness`
> 契约对齐：小程序前端 `xuanshiai-vue/docs/ai-profile-thoughtfulness-api.md`（前端 `USE_MOCK=false` 后直连，无需改 UI）
> 变更记录：2026-09-07 新增。

## 接口一：`GET /api/v1/ai/profile/thoughtfulness`

**基本信息**：获取当前用户最新一次 AI 用心度评审结果。完整 URL `https://<host>/api/v1/ai/profile/thoughtfulness`；Method `GET`；需要登录（Bearer Token，无会员要求）；Content-Type 响应为 `application/json`；成功状态码 `200`。

**请求参数**：无 query/path 参数，无请求体。

**请求示例**：

```
GET /api/v1/ai/profile/thoughtfulness HTTP/1.1
Authorization: Bearer <access_token>
```

**返回参数**：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `score` | integer | 是 | AI 评审用心度，0-100 整数。是「资料用心程度」指标，与完整度 `GET /users/me/completion` 的 score 互相独立 |
| `summary` | string | 是 | 1-2 句中文总结，描述资料整体填得好/不好的地方 |
| `todos` | array | 是 | 待优化清单，按 priority 从高到低排序；资料已经很好时为空数组 `[]` |
| `todos[].key` | string | 是 | 字段标识，枚举：`basic_info`/`self_intro`/`qa_answers`/`interest_tags`/`personality_tags`/`mbti`/`avatar`/`photos`，前端用它跳转到对应编辑区 |
| `todos[].label` | string | 是 | 中文展示名，如「自我介绍」 |
| `todos[].advice` | string | 是 | 一句具体的修改建议，不超过 300 字 |
| `todos[].priority` | string | 是 | 枚举：`high`/`medium`/`low` |
| `generated_at` | datetime | 是 | 评审生成时间（UTC，ISO 8601） |

**返回示例**（成功）：

```json
{
  "score": 76,
  "summary": "你的资料整体真诚自然，但自我介绍偏短、兴趣标签偏少，用心程度还有提升空间。",
  "todos": [
    {"key": "self_intro", "label": "自我介绍", "advice": "目前 42 字偏短，建议补充一个具体的生活场景或周末常态。", "priority": "high"},
    {"key": "interest_tags", "label": "兴趣标签", "advice": "兴趣标签只有 2 个，建议补充到 5 个以上。", "priority": "medium"}
  ],
  "generated_at": "2026-09-07T09:30:00"
}
```

**返回示例**（从未评审，`404`）：

```json
{"detail": "尚未进行 AI 用心度评审"}
```

**错误**：

| 状态码 | 触发条件 | 前端处理建议 |
| --- | --- | --- |
| `401` | 未登录 / token 失效 | 引导登录 |
| `404` | 该用户从未生成过评审记录 | 按「未评审」空态处理，展示「开始分析」入口，不是错误 |
| `500` | 服务内部错误 | 提示稍后重试 |

## 接口二：`POST /api/v1/ai/profile/thoughtfulness`

**基本信息**：触发 AI 用心度（重）分析并落库。完整 URL `https://<host>/api/v1/ai/profile/thoughtfulness`；Method `POST`；需要登录（Bearer Token，无会员要求，见下方业务规则）；Content-Type `application/json`；成功状态码 `200`。

**请求参数**：

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验规则 | 业务含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `trigger` | body | string | 是 | `save` | 枚举 `save`/`manual` | `save`=保存资料后自动触发；`manual`=用户在编辑页手动重新分析 |
| `edited_keys` | body | string[] | 否 | `[]` | 最多 20 项，每项须为合法 key（非法项被忽略） | 本次保存发生变化的字段集合，用于 AI 聚焦分析；key 枚举同返回的 `todos[].key` |
| `analysis_run_id` | body | string | null | 否 | `null` | 最长 96 字符；客户端每次重新分析生成新的值 | 本次模型运行标识，用于防止代理/服务端误重放，并在模型复写时区分分析轮次 |

**请求体示例**（合法）：

```json
{"trigger": "save", "edited_keys": ["self_intro", "interest_tags"], "analysis_run_id": "thoughtfulness-mfy2-abc123"}
```

**请求体示例**（非法：trigger 超出枚举，返回 422）：

```json
{"trigger": "auto"}
```

**返回参数**：与 GET 响应结构完全一致（评审完成后返回最新结果）。

**返回示例**：同 GET 成功示例。

**使用方法与业务规则**：

- **前置条件**：已登录。资料由后端自行读库评审（`users` + `profiles` + `user_media`），前端不传资料内容，防篡改。
- **调用顺序**：前端在「保存资料」接口成功后调用本接口（trigger=save）；或用户在编辑页点「重新分析」（trigger=manual）。
- **会员规则（有意差异化）**：阶段一其他 AI 功能仅会员可用；用心度**不设会员墙**——它是编辑页全员基础工具，且未来将作为「首页浏览他人资料」的门槛指标（score > 70 放行），设会员墙会导致非会员永远无法过门槛。后续如产品确认收费，需同步调整门槛规则。
- **频率/额度**：每日限额 `ai_daily_thoughtfulness_limit`（默认 10 次/天），Redis 原子扣减，UTC 日期重置；超限返回 `429`。GET 不消耗额度。
- **幂等与防重**：同用户评审结果按 `user_id` 唯一 UPSERT，重复触发只覆盖最新结果，不产生多行；每次重新分析携带新的 `analysis_run_id`，后端会把上一版输出传给模型作为排除条件，若模型复读则追加一次明确重写请求。
- **边界场景**：`edited_keys` 中非法 key 被静默忽略；AI 输出的 todos 中非法 key/空 advice 被过滤；资料极空的账号正常返回低分与建议；AI 服务不可用返回 `503`，不落库。
- **文案红线**：AI prompt 已约束——不承诺交友/婚恋结果，不制造焦虑或施压。

**错误**：

| 状态码 | 触发条件 | 错误响应示例 | 前端处理建议 |
| --- | --- | --- | --- |
| `401` | 未登录 | `{"detail": "请先登录"}` | 引导登录 |
| `404` | 用户资料不存在（理论上登录用户不会触发） | `{"detail": "用户资料不存在"}` | 提示重试 |
| `422` | trigger/edited_keys 校验失败 | `{"detail": [...]}` | 检查参数 |
| `429` | 当日额度耗尽 | `{"detail": "今日 AI 使用次数已用完"}` | 提示明日再试或减少手动重试 |
| `503` | AI 服务或 Redis 不可用 | `{"detail": "AI 服务暂时不可用"}` | 展示重试入口 |

## 数据存储

表 `ai_profile_thoughtfulness`（`database_setup_marriage.py`，启动自动建表）：`user_id` 唯一键，`score`/`summary`/`todos json`/`edited_keys json`/`model_name`/`created_at`/`updated_at`。

## 文档自检清单

- [x] 请求参数表含业务含义
- [x] 完整请求体示例（含非法示例）
- [x] 返回参数表字段展开
- [x] 成功返回示例
- [x] 使用方法与业务规则（前置条件/调用顺序/幂等/限流/边界）
- [x] 错误码表
