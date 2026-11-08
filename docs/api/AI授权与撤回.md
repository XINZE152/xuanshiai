# AI 授权与撤回

> **版本：** 1.0（2026-08-16，Task 3 / G2-A）
> **契约来源：** 统一实施方案 §6.3、§11.2；`app/services/ai/consents.py`

## 基本信息

| 项 | 值 |
|---|---|
| 前缀 | `/api/v1/ai` |
| 认证 | `Authorization: Bearer <access_token>` |
| Content-Type | `application/json` |
| scope 枚举 | `profile_text_extract`、`search_parse`、`compatibility_shadow`、`compatibility_display` |
| policy_revision | `ai-policy-2026-08-07-v1`（当前冻结值） |

### per-scope consent_version 注册表

每个 scope 有独立的冻结 consent_version，客户端必须提交匹配的值：

| scope | consent_version | 用途 |
|---|---|---|
| `profile_text_extract` | `profile-text-v1` | M04 画像文字抽取 |
| `search_parse` | `search-parse-v1` | M03 搜索条件解析 |
| `compatibility_shadow` | `compatibility-shadow-v1` | M06 兼容度 shadow 计算 |
| `compatibility_display` | `compatibility-display-v1` | M06 兼容度结果外显（`display_eligible` 门禁） |

---

## 1. 查询当前授权

**`GET /api/v1/ai/consents`**

需要登录。返回当前用户所有活跃（未撤回）授权。

### 返回参数

| 字段 | 类型 | 必返 | 含义 |
|---|---|---|---|
| `consents` | array | 是 | 活跃授权列表 |
| `consents[].scope` | string | 是 | 授权 scope |
| `consents[].version` | string | 是 | 授权时的 consent_version |
| `consents[].policy_revision` | string | 是 | 授权时的 policy_revision |
| `consents[].granted_at` | datetime | 是 | 授予时间（UTC ISO 8601） |
| `privacy_revision` | integer | 是 | 当前 privacy revision |

### 成功响应示例

```json
{
  "consents": [
    {
      "scope": "profile_text_extract",
      "version": "profile-text-v1",
      "policy_revision": "ai-policy-2026-08-07-v1",
      "granted_at": "2026-08-16T10:00:00"
    }
  ],
  "privacy_revision": 3
}
```

无活跃授权时返回 `{"consents": [], "privacy_revision": 0}`。

---

## 2. 授予授权

**`PUT /api/v1/ai/consents/{scope}`**

### 请求参数

| 参数 | 位置 | 类型 | 必填 | 校验规则 | 含义 |
|---|---|---|---|---|---|
| `scope` | path | string | 是 | 枚举（见上表） | 授权 scope |
| `Idempotency-Key` | header | string | 是 | 8-128 位 ASCII `[A-Za-z0-9._:-]` | 幂等键 |
| `X-Expected-Privacy-Revision` | header | integer | 是 | ≥ 0 | 当前 privacy revision |
| `consent_version` | body | string | 是 | 必须匹配 per-scope 冻结值 | 授权文案版本 |
| `policy_revision` | body | string | 是 | 必须匹配 `ai-policy-2026-08-07-v1` | 策略版本 |

### 请求示例

```http
PUT /api/v1/ai/consents/profile_text_extract HTTP/1.1
Authorization: Bearer <access_token>
Idempotency-Key: consent-grant-20260816-01
X-Expected-Privacy-Revision: 0
Content-Type: application/json

{"consent_version": "profile-text-v1", "policy_revision": "ai-policy-2026-08-07-v1"}
```

### 成功响应（200）

```json
{
  "operation_id": "abc123...",
  "scope": "profile_text_extract",
  "operation": "grant",
  "status": "active",
  "consent": {
    "scope": "profile_text_extract",
    "version": "profile-text-v1",
    "policy_revision": "ai-policy-2026-08-07-v1",
    "granted_at": "2026-08-16T10:00:00"
  },
  "cleanup_task_id": null,
  "privacy_revision": 1
}
```

### 幂等性

- 同一 `Idempotency-Key` + 相同 payload 重复请求：返回第一次的结果（`200`）。
- 同一 `Idempotency-Key` + 不同 payload：返回 `409 AI_CONSENT_IDEMPOTENCY_CONFLICT`。

---

## 3. 撤回授权

**`DELETE /api/v1/ai/consents/{scope}`**

### 请求参数

| 参数 | 位置 | 类型 | 必填 | 含义 |
|---|---|---|---|---|
| `scope` | path | string | 是 | 授权 scope |
| `Idempotency-Key` | header | string | 是 | 幂等键 |
| `X-Expected-Privacy-Revision` | header | integer | 是 | 当前 privacy revision |

### 成功响应（202）

```json
{
  "operation_id": "def456...",
  "scope": "profile_text_extract",
  "operation": "revoke",
  "status": "revoked",
  "consent": null,
  "cleanup_task_id": "task_01J...",
  "privacy_revision": 2
}
```

撤回响应返回前，该 scope 下所有草稿、快照、结果和投影同步不可读。异步 cleanup task 负责物理清理（开发/测试环境 15 分钟内）。

### 撤回传播范围

| scope | 同步不可读范围 |
|---|---|
| `profile_text_extract` | 画像草稿/会话、特征投影、搜索结果、兼容度快照 |
| `search_parse` | 搜索草稿、搜索快照、搜索结果 |
| `compatibility_shadow` | 兼容度快照 |
| `compatibility_display` | 兼容度结果外显：读取时 `display_eligible` 强制置 `false`（`app/services/ai/compatibility.py:1574-1579`），快照数据本身保留 |

---

## 4. 错误码

| HTTP | 业务码 | 触发条件 | retryable | 前端处理建议 |
|---|---|---|---|---|
| 400 | `AI_INPUT_INVALID` | scope 非法、Idempotency-Key 格式不合法 | false | 定位错误字段，不重试 |
| 409 | `AI_CONSENT_VERSION_CONFLICT` | consent_version 或 policy_revision 不匹配冻结值；privacy revision 过期 | false | 重新拉取 privacy revision，使用正确版本重试 |
| 409 | `AI_CONSENT_IDEMPOTENCY_CONFLICT` | 同一 Idempotency-Key 用于不同请求 | false | 提示操作冲突，使用新 key 重试 |
| 503 | `AI_FEATURE_DISABLED` | 生产 AI 开关、合规、保留期或 Provider 未批准 | false | 展示稳定禁用状态 |

### 错误响应示例

```json
{
  "detail": {
    "code": "AI_CONSENT_VERSION_CONFLICT",
    "message": "consent_version does not match the frozen value (profile-text-v1)",
    "request_id": "req_01J...",
    "retryable": false,
    "retry_after_ms": 0
  }
}
```

---

## 5. 使用方法与业务规则

### 前置条件
- 用户已登录且手机号已验证。
- grant 前必须先 `GET /consents` 获取当前 `privacy_revision`，以此作为 `X-Expected-Privacy-Revision`。

### 调用顺序
1. `GET /consents` → 获取 `privacy_revision`
2. `PUT /consents/{scope}` → 授予（使用对应 scope 的 `consent_version`）
3. 使用 AI 功能（画像/搜索/匹配度）
4. `DELETE /consents/{scope}` → 撤回（响应返回前数据不可读）

### 幂等与防重
- grant 和 revoke 都支持 `Idempotency-Key` 幂等。
- 同 key + 同 payload 返回第一次结果；同 key + 不同 payload 返回 `409`。
- grant 同一 (user, scope) 下已有活跃授权时，先撤旧再授新（原子操作）。

### 状态流转
- grant：`无授权 → active`
- revoke：`active → revoked`（不可逆，需重新 grant）
- revoke 后排队中的任务在下一安全点转为 `cancelled`/`superseded`，不再调用 Provider。

### 边界场景
- 已撤回的 scope 再次 revoke：返回 `status=already_revoked`，privacy revision 不变。
- privacy revision 过期：返回 `409 AI_CONSENT_VERSION_CONFLICT`，前端重新拉取后重试。

---

## 6. `profile_text_extract` 与 `search_parse` 的边界（AIT-11）

> 背景：知遇「说要求找对象」入口出现 `403 AI_CONSENT_REQUIRED`，根因是账号只有 `profile_text_extract` 授权、没有 `search_parse`（AIT-05）。本节定义两个授权的边界，供前端做授权引导与文案，后端接口不变。

### 6.1 一句话区分

| 授权 | 管的是哪一侧 | 撤回后的直接效果 |
|---|---|---|
| `profile_text_extract` | **本人资料被 AI 使用**：从聊天/输入文字抽取本人画像，并生产本人特征投影供检索与匹配度消费 | 本人不再出现在他人的检索结果与匹配度里；本人的画像草稿转 `deleted` |
| `search_parse` | **本人的检索动作**：把「说出来的择偶要求」解析成结构化条件并执行检索 | 本人的搜索草稿立即转 `expired`，旧草稿不可继续确认 |

两者是**平行、独立、可分别授予与撤回**的关系，不存在包含或派生。

### 6.2 冻结参数

| scope | consent_version（提交时必须逐字一致） | policy_revision | 代码出处 |
|---|---|---|---|
| `profile_text_extract` | `profile-text-v1` | `ai-policy-2026-08-07-v1` | `app/services/ai/consents.py:34-39`、`app/services/ai/profile.py:101-102` |
| `search_parse` | `search-parse-v1` | `ai-policy-2026-08-07-v1` | `app/services/ai/consents.py:34-39`、`app/services/ai/search.py:104-105` |

scope 枚举定义：`app/schemas/ai_common.py:32`、`app/services/ai/consents.py:64-68`（不在枚举内 → `400 AI_INPUT_INVALID`）。

### 6.3 门禁点与响应码（代码实测）

| 操作 | 校验的授权 | 未授权 / 已撤回时的响应 | 代码出处 |
|---|---|---|---|
| `POST /ai/search-drafts` | 仅 `search_parse` | `403 AI_CONSENT_REQUIRED`，message「尚未同意 AI 搜索解析授权」 | `app/services/ai/search.py:1101-1103`、`193-201` |
| `PATCH /ai/search-drafts/{id}` | 不校验授权 | 草稿不处于待确认态（含撤回后被置 `expired`）→ `409 RESULT_STALE`「草稿当前不可编辑（需处于待确认状态）」 | `app/services/ai/search.py:1376`、`226-230` |
| `POST /ai/search-drafts/{id}/confirm` | **不校验授权**（靠草稿状态兜底） | 撤回后草稿 `expired` → `409 RESULT_STALE`「草稿已过期」 | `app/services/ai/search.py:1473-1476`、`226-230` |
| `GET /ai/search-snapshots/{id}/results` | 读取期复检**候选**的可见性（含候选本人 `profile_text_extract`） | 候选撤回后从结果中剔除，`items` 变少或为空，不报错 | `app/services/ai/search.py:18-21` 模块头、`tests/integration/ai/test_ai_privacy_matrix.py:384-396` |
| `GET /ai/search-suggestions` | `search_parse`（仅约束 `source='ai'` 缓存命中） | 标签回显 `source='tags'` 不受门禁影响 | `app/services/ai/search.py:2846-2858` |
| `POST /ai/search-suggestions/generate` | 仅 `search_parse` | `403 AI_CONSENT_REQUIRED` | `app/services/ai/search.py:3201-3204` |
| 画像文字抽取 | `profile_text_extract` | 见本文第 2、3 节 | `app/services/ai/memory/consent_producers.py:30`、`55` |

两点必须让前端知道：

1. **`confirm` 端不重复校验授权**。撤权之所以能立刻让旧草稿不可用，靠的是撤回时把该用户的 `ai_search_draft` 批量置 `expired`、`ai_search_snapshot` 批量置 `invalidated`（`app/services/ai/consents.py:326-340`）。因此撤权后的正确错误码是 **`409 RESULT_STALE`（不是 403）**，前端不要把 409 也当作「未授权」再弹一次授权框。回归见 `tests/test_ai_search.py::test_revoked_search_parse_consent_makes_draft_unconfirmable`。
2. **一次授权不覆盖另一件事**。已授予 `profile_text_extract` 的用户调用搜索仍然会拿到 403；`search_parse` 不会自动带上画像抽取，反之亦然，不得做静默扩展。

### 6.4 撤回方向差异（容易混淆）

| 撤回动作 | 影响 |
|---|---|
| 撤回**自己**的 `search_parse` | 自己的搜索草稿 `expired`、快照与结果不可读（`app/services/ai/consents.py:326-331`） |
| 撤回**自己**的 `profile_text_extract` | 自己的画像草稿 `deleted`（`app/services/ai/consents.py:275-280`），且自己从他人的检索结果中被剔除 |
| 他人撤回 `profile_text_extract` | 本人仍能搜索，只是该候选不再出现，表现为 `items` 变少或为空，**不是错误** |

### 6.5 前端授权引导与文案（措辞待产品确认）

**触发**：知遇页用户说出择偶要求 → 前端调 `POST /ai/search-drafts` → 收到 `403` 且 `detail.code = AI_CONSENT_REQUIRED` → 展示二次授权弹层，不要显示错误页，也不要静默重试。

**弹层要素**（三条缺一不可，且不得承诺关系结果）：
- 用途：只把这次说出来的要求解析成筛选条件，用于找候选；
- 边界：不会把这次的要求写进你的长期择偶偏好或画像资料；
- 可撤回：随时可撤，撤回后本次条件草稿与结果立即不可再用。

**请求**：

```http
PUT /api/v1/ai/consents/search_parse HTTP/1.1
Authorization: Bearer <access_token>
Idempotency-Key: zhiyu-search-consent-<本地唯一串>
X-Expected-Privacy-Revision: <上一步 GET /ai/consents 返回的 privacy_revision>
Content-Type: application/json

{"consent_version": "search-parse-v1", "policy_revision": "ai-policy-2026-08-07-v1"}
```

授予前先 `GET /ai/consents` 取 `privacy_revision`（本文第 5 节）；授予成功后重发搜索请求。`403` 发生在写入 `ai_task` 之前，未消费的 `Idempotency-Key` 可以原样复用（`app/services/ai/search.py:1100-1107`：先校验输入与授权，之后才按 key 查已有任务；查 key 是纯读 `ai_task`，见 `_find_write_task` `app/services/ai/search.py:881-902`）。

**撤回入口**：授权管理页需分别展示两个 scope 的状态与撤回按钮（`DELETE /ai/consents/{scope}`），不得合并成一个「AI 功能总开关」。

> 上述三条要素是给前端的**结构要求**；具体用户可见措辞由产品确认后定稿，本小节不代表已批准的文案。禁止出现的表述：命中注定、最后机会、脱单焦虑类，以及任何对关系结果的承诺。

### 6.6 回归口径

- 未授予 `search_parse` → `POST /ai/search-drafts` `403 AI_CONSENT_REQUIRED`。
- 授予后 → 同一句要求可解析出草稿。
- 撤回后 → 旧草稿 `status='expired'`，继续 `confirm` 得 `409 RESULT_STALE`；新的解析请求重新回到 `403`。
- 意图隔离：走知遇会话说出择偶要求，不写入 `personal` / `ideal_partner` 画像草稿（口径见 `docs/待完成事项.md` AIT 小节与知遇定稿 v2 §3）。

---

## 变更记录

- 2026-11-08（AIT-11，知遇「说要求找对象」· 后端配合定稿 v2 §4.1）：新增第 6 节，定义 `profile_text_extract` 与 `search_parse` 的边界、门禁点与前端二次授权口径。
  - 变更前：本文只有 scope 注册表与撤回传播范围，没有说明「一次授权不覆盖另一件事」，也没有说明撤权后继续 `confirm` 的真实错误码；前端按 403 兜底会把 409 也误判成未授权。
  - 变更后：补 §6.3 门禁表（明确 `confirm`/`PATCH` 不校验授权、靠草稿 `expired` 兜底）、§6.4 撤回方向差异、§6.5 引导与请求示例（文案措辞标注为待产品确认）、§6.6 回归口径。
  - 影响范围：**纯文档新增**，接口 URL/Method/参数/错误码/响应结构均未变化，已按前 5 节实现的客户端不受影响。
- 2026-11-08（AIT-10，部署前复核）：核对线上 `https://xhztest.xyz/openapi.json` 与本文所列 scope、`PUT`/`DELETE /ai/consents/{scope}` 两个必选 Header 逐字一致；M03/M07 全部端点线上已部署。详见 `docs/validation/2026-11-08-线上契约复核-知遇M03-M07-授权.md`。
- 2026-11-07（AIT / BE-04，文档漂移修正）：scope 枚举、per-scope consent_version 注册表、撤回传播范围三处补齐第 4 个 scope `compatibility_display`（`compatibility-display-v1`），与代码 `app/schemas/ai_common.py:32`、`app/services/ai/consents.py:34-39` 逐字对齐。
  - 变更前：文档只列 3 个 scope，前端按文档实现会漏掉 `compatibility_display`，误判为「scope 非法」。
  - 变更后：4 个 scope 全部列出，并补充该 scope 的撤回语义（只关外显，不删快照）。
  - 影响范围：**纯文档修正**，接口 URL/Method/参数/错误码均未变化，已按 3 个 scope 实现的客户端行为不受影响。
- 2026-08-16：v1.0 首版（Task 3 / G2-A），冻结 3 个 scope。
- 并发 grant/revoke：`FOR UPDATE` 锁定 `user_revision_state`，只允许一个提交。
