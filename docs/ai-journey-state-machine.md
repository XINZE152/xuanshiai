# 墨相师旅程状态转换（Task 8）

## 两条状态轴

`ai_profile_session.journey_stage` 是发布进度轴，固定为 `chatting → building → ready → published`。
它只允许单调前进或同态幂等重放；为兼容历史发布路径，允许从任一较早阶段直接跳到更晚阶段。

暂停/唤醒属于 `ai_profile_session.status` 的交互状态（`paused` ↔ `draft`/`awaiting_confirmation`），
不改变 `journey_stage`。邀请 snooze 是唯一会让旅程阶段回到较早值的显式交互边：`building → chatting`。
因此不能把所有 `UPDATE journey_stage` 都套用发布轴的单调推进函数。

## 转换表

| 事件 | 当前 | 目标 | 合法性 | 副作用 |
| --- | --- | --- | --- | --- |
| `advance` | 任一阶段 | 同阶段或更晚阶段 | 合法 | 仅更新阶段；不自动发 outbox |
| `snooze` | `building` | `chatting` | 合法 | 邀请置 `snoozed`，提交后 WS 推送 `build_invite_resolved` |
| `snooze` | 其他阶段 | 任意 | 非法 | `JOURNEY_STAGE_TRANSITION_INVALID`（路由现映射为 `AI_INPUT_INVALID`） |
| `pause` | 任一阶段 | 同阶段 | 合法（交互占位） | 实际写 `status=paused`，阶段保持不变 |
| `wake` | 任一阶段 | 同阶段 | 合法（交互占位） | 实际写 `status=draft/awaiting_confirmation`，阶段保持不变 |
| `advance` 回退 | 较晚阶段 | 较早阶段 | 非法 | `JOURNEY_STAGE_TRANSITION_INVALID` |
| `advance` | 未知当前阶段 | 合法当前阶段 | 兼容归一为 `chatting` | 保持历史读取容错；不会写入未知值 |
| 任意事件 | 未知目标阶段/事件 | 任意 | 非法 | `JOURNEY_STAGE_TRANSITION_INVALID` |

实现：`transition_journey_stage()` 负责事件表校验，`advance_journey_stage()` 仅负责发布轴。交互事件对未知当前/目标阶段一律拒绝；`advance` 保留历史的未知当前阶段归一兼容，但未知目标仍拒绝。

## 并发与 revision

- 邀请创建和 snooze 使用 `session → invite`：先在同一事务对
  `ai_profile_session` 执行 `SELECT ... FOR UPDATE`，读取锁定行的真实
  `journey_stage` 后才调用 `transition_journey_stage()`，随后才锁定/更新邀请。
- accept 会同时触及草稿，使用 `draft → session → invite`：先锁定已有的
  `ai_profile_draft`，再锁 session 读取真实阶段，最后锁 pending invite。
  `publish_profile_draft` 同样是 `draft → session`，因此二者不存在
  `session ↔ draft` 的反向锁环；无既有草稿时，accept 在拿到 session 锁后才插入新草稿。
- 锁定行若已是 `journey_stage='published'`、`status='published'` 或
  `active_status=0`，创建邀请返回空结果，accept/snooze 拒绝为“会话已结束”，
  不得把终态写回 `building`/`chatting`。同一邀请的 accept/snooze 仍通过
  `status='pending'` 条件更新保证只能有一个事务成功，重复请求返回“该邀请已处理”。
- snooze 仅在锁定行真实处于 `building` 时执行 `building → chatting`；它是合法的
  交互回退，不适用于已发布或其他阶段。
- 接受邀请创建草稿时，后续字段确认/发布沿用 `ai_profile_draft.expected_revision` 乐观锁；
  发布只接受 confirmed 字段，并由既有 profile revision/outbox 链路递增对应主体版本。
- 当前 `ai_profile_session` 没有独立 journey revision；阶段更新不新增公开字段或接口。
  会话的 profile/preference revision 快照仍由 `load_owned_active_session` 校验，变化时标记 stale，
  防止旧会话继续写入。

## Outbox 与通知

- `advance`、pause、wake、snooze 本身不写 derivation outbox；它们是会话交互/展示状态。
- 候选达到门槛时，服务在同一事务创建 pending 邀请，提交后通过现有 WebSocket 推送 `build_invite`。
- snooze/accept 提交后推送 `build_invite_resolved`；accept 另推送确认卡。
- 真正发布画像时，`publish_profile_draft` 继续写既有 profile revision 与 derivation outbox，
  不由本状态机重复入队。
