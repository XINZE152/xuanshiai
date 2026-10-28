# 客源 AI 智能录入（文本抽取）接口

> 2026-10-28 新增。用途：红娘在客源录入表单粘贴一段自由文本（聊天记录、来电备忘、名片信息等），
> 后端用 AI 解析出结构化客源字段并回传，供前端**预填表单**。本接口**只读、不落库**。
>
> 说明：本期只做「文本→字段」。图片 OCR 需引入视觉/多模态 provider，作为二期，未在本接口范围内。

## 通用约定

前缀 `/api/v1`，需 `Authorization: Bearer <access-token>`，后台账号须具有 `customer_lead.manage` 或 `*` 权限，
与既有客源录入接口同域同权限。请求/响应均为 `application/json`。

生成统一走项目 AI 网关（`app/services/ai_provider.complete`，场景 `lead_extract`），受 AI 总开关与
生产审批门禁约束；未启用/未配置真实模型时按既有语义 fail-closed 返回 `503`。

---

## `POST /admin/customer-leads/ai-extract`

**基本信息**

| 项 | 值 |
|---|---|
| 用途 | 自由文本 → 结构化客源字段（仅回传，不写库） |
| URL | `/api/v1/admin/customer-leads/ai-extract` |
| Method | `POST` |
| 需要登录 | 是 |
| 所需权限 | `customer_lead.manage`（或 `*`） |
| 请求 Content-Type | `application/json` |
| 成功状态码 | `200` |

**请求参数**

| 参数 | 位置 | 类型 | 必填 | 默认 | 校验 | 含义 |
|---|---|---|---|---|---|---|
| `text` | body | string | 是 | 无 | 长度 `1..4000` | 待抽取的自由文本 |

- 合法示例：`{"text":"小李 男 138-0013-8000 抖音来的，聊得不错约了周末到店"}`
- 非法示例：`{"text":""}` → `422`；`{"text":"<4001字>"}` → `422`。

**请求示例**

```
POST /api/v1/admin/customer-leads/ai-extract
Authorization: Bearer <access-token>
Content-Type: application/json

{ "text": "小李 男 138-0013-8000 抖音来的，F类预约需二邀" }
```

**返回参数**

顶层 `LeadExtractResult`：

| 字段 | 类型 | 必返 | 含义 |
|---|---|---|---|
| `fields` | object | 是 | 解析出的客源字段候选，见下表；文本未命中的字段为 `null` |
| `confidence` | object(string→number) | 是 | 每个**已抽取**字段的置信度 `0~1`；模型未给或字段未命中则不出现在此表 |
| `snippets` | object(string→number/string) | 是 | 每个**已抽取**字段对应的原文片段（≤120 字），供红娘核对 |
| `notes` | array[string] | 是 | 歧义/缺失告警（如手机号位数不足、意向无法判定），无则 `[]`，最多 10 条 |

`fields`（`LeadExtractedFields`，字段口径与 `CustomerLeadCreate` 对齐，全部可选）：

| 字段 | 类型 | 空值含义 | 取值说明 |
|---|---|---|---|
| `name` | string\|null | 文本未提及 | 客源称呼/姓名，截断至 128 |
| `phone` | string\|null | 文本未提及或非法 | 手机号；去空格/连字符、允许前导 `+`，仅数字，截断至 32 |
| `wechat` | string\|null | 文本未提及 | 微信号，截断至 128 |
| `source` | string\|null | 文本未提及 | 来源渠道，截断至 64 |
| `intention_level` | int\|null | 无法判断 | 客户意向 **A-G 七档** `1..7`（`1`A未接…`7`G已到店未签约）。模型若回字母 `A-G` 或数字串，服务端归一为 `1..7`；越界（如 `9`）归 `null` |
| `gender` | int\|null | 无法判断 | `0` 未知 / `1` 男 / `2` 女；接受 `男/女/male/female/0/1/2` 归一 |
| `remark` | string\|null | 文本未提及 | 备注/其它信息，截断至 2000 |

**返回示例（成功）**

```json
{
  "fields": {
    "name": "小李",
    "phone": "13800138000",
    "wechat": null,
    "source": "抖音",
    "intention_level": 6,
    "gender": 1,
    "remark": null
  },
  "confidence": { "name": 0.9, "phone": 0.95, "intention_level": 0.7 },
  "snippets": { "phone": "138-0013-8000", "source": "抖音来的" },
  "notes": ["微信号未从文本识别到"]
}
```

**空结果示例**（模型未识别到任何字段，合法、非错误）

```json
{ "fields": { "name": null, "phone": null, "wechat": null, "source": null, "intention_level": null, "gender": null, "remark": null }, "confidence": {}, "snippets": {}, "notes": [] }
```

**使用方法与业务规则**

- 前置条件：已登录且具 `customer_lead.manage`；AI 功能已启用（生产需 `ai_enabled`+三道审批门禁+真实 provider，非 mock）。
- 调用顺序：表单内点击「AI 智能识别」→ 调本接口 → 用返回 `fields` 预填 → 红娘核对/补全必填项 → 再调既有 `POST /admin/customer-leads` 创建。本接口**不创建任何客源**，不产生写入与审计。
- 预填建议：仅回填 `fields` 中非 `null` 项；对 `confidence` 低于阈值（如 `<0.6`）的字段前端高亮提示人工确认；`snippets` 可作悬浮来源溯源；`notes` 作为表单顶部提示。
- 幂等/防重：本接口只读、无副作用，不需 `Idempotency-Key`；但受每日额度限制（见下）。
- 频率/额度：按后台账号每日限额 `ai_daily_lead_extract_limit`（默认 `20`）限流，超限返回 `429`；调用失败（含 AI 不可用、返回非法 JSON）自动退还当日额度。
- 数据范围/权限：不涉及按 lead_id 的资源归属校验（无既有资源），返回仅回传给发起抽取的账号本人。
- 敏感信息：入参文本与模型原文均不写日志、不落库；`phone/wechat` 仅原样返回给发起者用于回填，不进入审计。请勿在 `text` 中粘贴与录入无关的他人敏感证件信息。
- 兼容性：纯新增 URL，不改动任何既有客源或 AI 接口契约。

**错误**

| HTTP | 触发条件 | 响应示例 | 前端处理建议 |
|---|---|---|---|
| `401` | 未登录 | `{"detail":"..."}` | 跳登录 |
| `403` | 无 `customer_lead.manage` 权限 | `{"detail":"..."}` | 提示无权限 |
| `422` | `text` 为空或超 4000 字 | FastAPI 校验错误体 | 提示文本长度 |
| `429` | 当日抽取次数超限 | `{"detail":"今日 AI 智能录入次数已用完"}` | 提示明日再试/联系管理员 |
| `503` | AI 未启用/未配置 key/provider 为 mock/调用失败/返回非法 JSON | `{"detail":"AI服务未启用"}` 等 | 降级为手动填写，勿重试风暴 |
