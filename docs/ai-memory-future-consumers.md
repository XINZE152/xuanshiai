# AI 军师 / AI 分身 — Future Consumer 边界登记（2026-09-05）

> Phase 2 计划 Task 8：只登记边界，不实现任何行为。
> 未来计划的「前端 Memory View + AI 军师/AI 分身真实消费者」以此为准。

## 1. 登记状态

| function_key | 用途 | 状态 |
| --- | --- | --- |
| `search` / `compatibility` / `recommend` | 下游匹配读取 | Phase 2 已接入（legacy→shadow→memory 切换） |
| `counselor_context` | AI 军师会话上下文 | **future key：登记，当前 fail closed（`FEATURE_NOT_ENABLED`）** |
| `persona_context` | AI 分身公开画像 | **future key：登记，当前 fail closed（`FEATURE_NOT_ENABLED`）** |

当前对 future key 的 grant / build / read_active 调用一律抛
`ProjectionFeatureNotEnabled`；不产生 grant 行、Projection 行或任何下游输入
（测试钉死：`tests/test_ai_memory_projection_policy.py`）。

## 2. AI 军师（counselor_context）最小输入与隐私门槛

- **允许输入**：当前用户自己的 `personal_profile`（本人已确认事实）与
  `ideal_partner_preference`（本人偏好）——只读 Projection 10 字段 allowlist；
- **禁止输入**：raw event、完整 transcript、source_quote、任何未确认
  Claim/Insight/State/Suppression、任何第三方主体资料；
- **隐私门槛**：`profile_text_extract` 授权快照有效 + 对应维度 grant active
  + policy revision 一致 + 投影 status=active；
- **行为边界**：军师不得改写记忆（写入只经 Core v1 Ledger），不得读取
  他人（非 owner）的任何投影。

## 3. AI 分身（persona_context）最小输入与隐私门槛

- **允许输入**：当前可见且已授权的 `public_profile_summary`——仅本人
  （personal 主体）的已确认公开摘要；
- **禁止输入**：raw event、完整 transcript、source_quote、
  `personal_profile`/`compatibility_features`/`ideal_partner_preference`
  等非公开维度、任何他人资料；
- **隐私门槛**：同上（consent + grant + policy + status），另加
  「公开可见性」门（复用现有资料可见性判定，非 Phase 2 新增）；
- **行为边界**：分身对外只输出 public_profile_summary 允许的字段；
  ideal_partner 永远是当前用户自己的偏好，绝不出现在分身对外输出中。

## 4. 实现入口（未来计划引用，本期不存在）

- 注册表：`app/services/ai/memory/projection_policy.py::FUTURE_CONSUMER_REGISTRY`；
- 启用判定：`ProjectionPolicy.assert_function_enabled`（future key 抛
  `ProjectionFeatureNotEnabled`）；
- 未来启用 = 在该注册表基础上新增 function_key 枚举值 + 对应 grant/build/read
  流程 + 专门的产品与授权评审（另立项），**不需要**改动 Core v1 账本或
  Phase 2 投影表结构。
