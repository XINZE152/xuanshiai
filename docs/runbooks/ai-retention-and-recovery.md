# AI 保留期清理与恢复运行手册（Runbook）

> 适用范围：AI 后端全部清理任务（语音临时音频、voice transcript、generation audit、
> derivation outbox、memory state TTL）、审计写入背压、WebSocket 通知回退与
> quota 退款的运维处置。
> 责任人：AI 后端值班。修订基准：四批次改进计划 Task 17（2026-09-10）。

---

## 1. 组件与调度总览

| 组件 | 进程 | 调度方式 | 关键配置前缀 |
| --- | --- | --- | --- |
| 业务任务 Worker | `python -m app.workers.ai_worker` | `_run_forever` 常驻轮询 | `AI_LEASE_*` / `AI_MAX_ATTEMPTS` |
| 清理消费者 | `python -m app.workers.ai_worker --consumers` | 独立进程轮询 outbox | `AI_DERIVATION_OUTBOX_*` |
| 语音临时音频清理 | 业务 Worker 内嵌 | `ai_voice_audio_cleanup_interval_seconds` 节流 | `AI_VOICE_AUDIO_*` |
| Memory State TTL 清理 | 业务 Worker 内嵌 | `ai_memory_state_ttl_cleanup_interval_seconds` 节流 | `AI_MEMORY_STATE_TTL_*` |
| Retention 清理（transcript/audit/outbox 终态行） | 业务 Worker 内嵌 | `ai_retention_cleanup_interval_seconds` 节流 | `AI_RETENTION_*` |
| 审计写入 flusher | API/Worker 进程内异步任务 | 有界队列（2048）+ 后台批量排水 | `AI_AUDIT_ENABLED` |

所有清理操作共享同一纪律：**独立会话/事务、批次独立提交、失败显式回滚、
绝不阻塞业务任务轮次、下一轮按间隔自动重试**。

## 2. 指标清单与告警阈值

指标通过 `emit_ai_metric` 写入进程内注册表并输出结构化日志行
（`ai_metric name=... value=... tags=...`）；生产以日志聚合（如 Loki/ELK）
作为时序出口。

| 指标 | 含义 | 建议告警阈值 | 处置入口 |
| --- | --- | --- | --- |
| `queue_age` | 任务从创建到被 claim 的秒数 | p95 > 60s 持续 5min | §4.1 |
| `lease_reclaimed` | reaper 回收的租约数 | > 0 且持续增长 | §4.2 |
| `task_retry_backlog` | retry_wait 积压（按 task_type） | > `AI_METRICS_BACKLOG_WARN_THRESHOLD`(默认 1000) | §4.3 |
| `retry_rate` | 单轮失败/进入重试次数 | 突增 > 10× 基线 | §4.4 |
| `provider_5xx` | provider 5xx 计数 | > 10/min | §4.4 |
| `provider_timeout` | provider 超时/网络故障计数 | > 20/min | §4.4 |
| `schema_invalid` | provider 输出违反冻结 schema | > 0 且持续 | §4.5 |
| `websocket_fallback` | Redis pub/sub 不可用退回轮询 | > 0 持续（Redis 健康时恒为 0） | §4.6 |
| `audit_lost` | 审计事件丢失（队列满/写入失败） | > 0 即告警（合规数据） | §4.7 |
| `voice_audio_cleanup_deleted` | 每轮删除的过期音频数 | 骤降为 0（上传正常时） | §4.8 |
| `voice_audio_cleanup_failed` | 每轮删除失败文件数 | > 0 持续 3 轮 | §4.8 |
| `voice_audio_cleanup_duration_seconds` | 每轮清理耗时 | > 30s | §4.8 |
| `retention_cleanup_deleted` | 每轮 retention 清理行数（transcript+audit+outbox） | 骤降为 0 | §4.9 |
| `memory_state_ttl_cleanup_success` | TTL 过期 State 数（无失败批次时） | —（信息项） | §4.10 |
| `memory_state_ttl_cleanup_failed` | TTL 失败批次数 | > 0 持续 3 轮 | §4.10 |
| `memory_state_ttl_cleanup_skipped` | 节流跳过轮次 | —（信息项） | — |
| `outbox_backlog` | derivation outbox 未消费积压 | > 阈值（默认 1000） | §4.11 |
| `deletion_propagation_seconds` | 删除事件平均传播延迟 | > 300s | §4.11 |
| `quota_refund_failure` | quota 退款失败次数 | > 0 即处置 | §4.12 |

## 3. 配置项速查（`.env`，前缀不区分大小写）

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `AI_VOICE_AUDIO_RETENTION_HOURS` | 24 | 临时 TTS 音频保留期（与 transcript 分开） |
| `AI_VOICE_AUDIO_CLEANUP_INTERVAL_SECONDS` | 3600 | 音频清理节流间隔 |
| `AI_VOICE_TRANSCRIPT_RETENTION_HOURS` | — | 转写文本保留期 |
| `AI_GENERATION_AUDIT_RETENTION_HOURS` | — | 审计行保留期 |
| `AI_DERIVATION_OUTBOX_SUCCEEDED_RETENTION_HOURS` | — | 终态 outbox 成功行保留期 |
| `AI_DERIVATION_OUTBOX_DEAD_LETTER_RETENTION_HOURS` | — | 死信行保留期 |
| `AI_RETENTION_CLEANUP_INTERVAL_SECONDS` | — | retention 清理节流间隔 |
| `AI_RETENTION_CLEANUP_BATCH_SIZE` | — | retention 单批行数上限 |
| `AI_MEMORY_STATE_TTL_BATCH_SIZE` / `_MAX_BATCHES` / `_TIME_BUDGET_SECONDS` | — | State TTL 清理三重上限 |
| `AI_MEMORY_STATE_TTL_CLEANUP_INTERVAL_SECONDS` | — | State TTL 节流间隔 |
| `AI_AUDIT_ENABLED` | true | 审计写入总开关 |
| `AI_METRICS_BACKLOG_WARN_THRESHOLD` | 1000 | 积压类指标本地告警阈值 |

敏感项（DB/Redis 凭据）见根 `README` 与 `.env.example`；本手册不含任何密钥。

## 4. 处置手册

### 4.1 队列延迟高（queue_age 超阈）

1. `python -m app.workers.ai_worker --once --dry-run` 确认 worker 活性与积压概况。
2. 检查 worker 进程存活（`docker ps` / 进程管理器），必要时重启 worker 进程
   （重启安全：未终态任务由租约 reaper 恢复，见 §4.2）。
3. 若 claim 正常但处理慢：查看 `provider_timeout`/`provider_5xx` 是否同涨
   （provider 侧问题 → §4.4）。

### 4.2 租约被反复回收（lease_reclaimed 持续增长）

1. 确认 worker 主机负载与 `AI_LEASE_SECONDS`（租约须大于 handler 正常耗时）。
2. 若某 `task_type` 反复被回收后终态 failed（attempt 耗尽），按 §4.3 查积压，
   用 §4.13 的手工重试命令在修复后重新入队。

### 4.3 重试积压（task_retry_backlog 超阈）

1. 按 `task_type` 维度定位积压来源（日志 `task_retry_backlog` 带 tag）。
2. `retry_wait` 行由 worker 按 `next_run_at` 自动重新 claim——**不要**手工改状态。
3. 重试耗尽（attempt ≥ max）仍滞留的任务由日志 `ai_task_retry_exhausted_stuck`
   点名，确认根因后按 §4.13 处理。

### 4.4 provider 超时/5xx（provider_timeout / provider_5xx）

1. 确认 provider 侧状态（阿里云/自建 LLM 服务健康页）。
2. 超时类失败自动按 retryable 退避重试；无需人工介入，等 provider 恢复。
3. 长时间不可恢复：按发布回滚流程关闭对应 AI 功能开关（见 §5 回滚），
   任务停止进入（queued 任务保持在队列，恢复后自动续跑）。

### 4.5 schema 违规（schema_invalid）

1. 该失败**不可重试**，任务终态 failed，`error_code=AI_SCHEMA_INVALID`。
2. 检查 provider 模型版本是否漂移（换模型/改提示词后最常见）。
3. 修复后按 §4.13 对受影响请求重放。

### 4.6 WebSocket 退回轮询（websocket_fallback > 0）

1. 每次打点代表一次「订阅失败 → 退回纯轮询」，功能不降级、仅延迟变差。
2. 检查 Redis 连通性（`redis-cli -p <port> ping`）。
3. Redis 恢复后自动回到 pub/sub 快路径；无手工步骤。

### 4.7 审计丢失（audit_lost > 0）

1. `reason=queue_full`：审计产生速率超过排水能力。检查 DB 写入延迟
   （慢查询/锁），必要时调大 flusher 排水（`_AUDIT_FLUSH_BATCH`，代码常量）。
2. `reason=write_failed`：pymysql 写入失败。确认 `database_url`、DB 可用性、
   `ai_generation_audit` 表存在（迁移 `scripts/manage_ai_migration.py`）。
3. **丢失的审计事件不可自动补偿**（原始上下文已随请求结束）。合规要求场景：
   用 provider 侧账单/日志对账，并在 `docs/待完成事项.md` 记录缺口与时间窗。
4. 进程内水位：`audit_queue_depth()`（当前积压）、`audit_lost_total()`（累计丢失）。

### 4.8 语音临时音频清理异常

1. `voice_audio_cleanup_failed > 0`：单文件删除失败被隔离，下一轮自动重试；
   排查文件占用/权限（Windows：句柄占用最常见）。
2. 日志 `voice_audio_cleanup_out_of_bounds_refused`：路径越界被拒绝——
   **安全行为**，确认是否有符号链接指向配置目录外，人工复核后处理。
3. 清理耗时陡增：检查目录文件堆积量（`upload_dir/voice/tts`、`upload_dir/tts`），
   必要时临时调小 `AI_VOICE_AUDIO_RETENTION_HOURS` 加速收敛（一次性，事后还原）。

### 4.9 retention 清理异常（transcript/audit/outbox 行）

1. 失败整轮回滚（`ai_retention_cleanup_round_failed`），下一轮自动重试。
2. 检查 DB 可用性与表存在性；批次大小可经 `AI_RETENTION_CLEANUP_BATCH_SIZE`
   调小以绕过大事务超时。
3. **数据删除不可逆**：retention 清理删除的是超过保留期的运营数据；
   误配置保留期（如 0）会立即大量删除——调整保留期前先在测试环境验证。

### 4.10 Memory State TTL 清理异常

1. `memory_state_ttl_cleanup_failed > 0`：失败批次独立回滚，成功批次已提交，
   下一轮从断点继续（keyset 分页），无需人工干预。
2. 长期失败：检查 `ai_memory_state` 表锁/慢查询；批次上限三件套可调小。

### 4.11 outbox 积压 / 删除传播延迟

1. 确认 `--consumers` 清理消费者进程存活（业务 Worker 不消费 outbox）。
2. 死信（`dead_letter` 状态）行为终态：修复根因后按 §4.13 手工重置重试。
3. 传播延迟高但无积压：检查单事件处理耗时日志（`derivation_outbox` 前缀）。

### 4.12 quota 退款失败（quota_refund_failure > 0）

1. 每次打点 = 用户一次使用次数未退还（Redis 不可用/脚本异常）。
2. 定位时间窗与受影响用户（日志含 `quota_key`，格式 `ai:<code>:<user_id>:<date>`）。
3. 补偿：确认当日该用户实际用量后，用 Redis `DECR`（或删除当日 key 重置）
   人工返还；操作前记录原值，双人复核。

### 4.13 手工重试命令（受控操作）

```powershell
# 单轮真实运行（观察 claim/complete/fail 概况；不做清理）
python -m app.workers.ai_worker --once

# 单轮清理消费者（处理 outbox 删除事件）
python -m app.workers.ai_worker --consumers --once

# 空转体检（绝不写库）
python -m app.workers.ai_worker --once --dry-run
```

对**单条**卡死任务的人工重置（须先冻结根因，禁止批量执行）：

```sql
-- 仅限 attempt 耗尽但根因已修复的任务；退回 retry_wait 让 worker 重新走
-- 完整状态机（queued 不经 leased 不可直接 running）。
UPDATE ai_task SET status = 'retry_wait', attempt_count = 0,
  next_run_at = UTC_TIMESTAMP(), lease_owner = NULL, lease_until = NULL
WHERE task_id = '<task_id>' AND status IN ('failed', 'retry_wait');
```

### 5. 回滚步骤

1. **功能级回滚（首选）**：关闭对应 `AI_*_ENABLED` 开关（配置中心/.env），
   重启 API/Worker。任务停止进入，存量任务由状态机自然收敛。
2. **发布级回滚**：回退到上一个发布提交（每个任务一个独立 commit，
   `git revert <commit>`），重启进程。所有 Task 提交均不含破坏性 schema 变更；
   若涉及迁移（如 memory projection 表），先跑
   `python scripts/manage_ai_migration.py --status` 确认版本再决定。
3. **清理任务回滚**：清理无「撤销」语义——回滚代码即停止后续删除；
   已删除的音频/transcript 数据按 §6 的备份策略恢复（如启用）。

## 6. 数据删除与恢复

| 数据 | 删除者 | 可恢复性 |
| --- | --- | --- |
| 临时 TTS 音频 | 语音清理（retention 到期） | 不可恢复（合规要求：不长期留存） |
| voice transcript | retention 清理 | 仅当启用 DB 备份时按备份点恢复 |
| generation audit | retention 清理 | 仅当启用 DB 备份时按备份点恢复 |
| 终态 outbox 行 | retention 清理 | 不可恢复（已消费的收据在 derivation_consumer_receipt） |
| memory state（业务 TTL 过期） | State TTL 清理（先写 state_expired 台账事件） | 通过台账事件审计；数据本身不可恢复 |

恢复操作纪律：只从**已验证的备份**恢复；恢复前在测试环境演练；恢复范围
必须限定到具体时间窗与主键区间，禁止整表导入覆盖线上。

## 7. 演练清单（每季度）

- [ ] 杀掉 worker 进程 → 确认 reaper 在 `AI_LEASE_SECONDS + slack` 内回收并重试。
- [ ] 停 Redis → 确认 `websocket_fallback` 打点、quota consume/refund 行为符合
      环境预期（开发/测试走本地 fallback，生产 fail-closed）。
- [ ] 停 MySQL → 确认 `audit_lost` 打点且业务请求不因审计失败而失败。
- [ ] 填满审计队列（临时调小 `_AUDIT_QUEUE_MAX`）→ 确认丢最旧 + `reason=queue_full`。
- [ ] 运行 `--once --dry-run` → 确认零写入。
