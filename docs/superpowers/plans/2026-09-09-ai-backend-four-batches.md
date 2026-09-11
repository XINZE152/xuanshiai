# AI 后端四批次改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. 每个任务完成后必须执行对应测试，再进入下一个任务。

**Goal:** 在不新增公开接口、保留新旧 AI 双栈的前提下，修复后端已确认的正确性、扣费、状态、清理和性能问题。

**Architecture:** 先处理数据安全和业务正确性，再处理契约与生命周期，最后进行 WebSocket、缓存、搜索和批量写入优化。所有高风险变更必须有独立测试、监控和回滚点。

**Tech Stack:** FastAPI、SQLAlchemy AsyncSession、MySQL、Redis、Worker、pytest、WebSocket、现有 AI Provider/Gateway。

# 全局约束

- 不新增公开 HTTP/WebSocket 接口。
- 旧 AI 栈只修复缺陷，不进行整体迁移。
- 新旧双栈继续并行运行。
- 不改变现有 API 字段名称、状态枚举和成功响应格式，除非任务明确要求并同步文档。
- 不把静态检查结果描述为生产验证结果。
- 每个任务单独提交，提交前运行该任务的 focused tests。
- 禁止使用 `git reset --hard`、`git checkout --`、`git clean` 清理工作区。
- 所有数据库、Redis、文件清理操作必须可重试、可观测、可回滚。

# 批次一：正确性、数据安全和扣费一致性

## Task 1: 修复语音文件清理

**Files**

- Modify: `app/core/config.py`
- Modify: `app/services/voice/conversation.py`
- Modify: `app/services/voice/providers.py`
- Modify: `app/workers/ai_worker.py`
- Test: `tests/test_voice_cleanup.py`

**实施步骤**

- [ ] 增加统一清理函数，明确扫描以下两个目录：

  - `upload_dir/voice/tts`
  - `upload_dir/tts`

- [ ] 清理函数必须校验最终路径位于配置目录内，拒绝路径穿越和越界符号链接。
- [ ] 音频文件 retention 与 `voice_transcript` retention 分开配置。
- [ ] 单个文件删除失败时记录错误并继续处理其他文件。
- [ ] 将清理函数接入现有 worker 定时任务。
- [ ] 增加过期、不过期、越界路径、重复执行、单文件失败测试。

**验收命令**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_voice_cleanup.py
```

**回滚**

回滚清理任务注册和配置变更；不得手工批量删除生产文件。

## Task 2: 接通 Memory State TTL 清理

**Files**

- Modify: `app/services/ai/memory/derivations.py`
- Modify: `app/workers/ai_worker.py`
- Test: `tests/test_ai_memory_ttl_cleanup.py`

**实施步骤**

- [ ] 使用独立 AsyncSession 执行过期状态清理。
- [ ] 每个批次独立提交；异常时显式 rollback。
- [ ] 设置批量上限和单次执行时间上限。
- [ ] 清理成功、清理失败、重复执行分别记录指标。
- [ ] 验证清理失败不会污染后续 outbox 或 worker 事务。

**验收命令**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ai_memory_ttl_cleanup.py
```

## Task 3: 修复流式审计异常

**Files**

- Modify: `app/services/voice/master_orchestrator.py`
- Modify: `app/services/ai/gateway.py`
- Test: `tests/test_stream_audit_failure.py`

**实施步骤**

- [ ] 在进入 `try` 前初始化 `provider_name`、`provider_model` 等审计字段。
- [ ] provider 初始化失败时，审计字段使用 `None` 或固定的 `unknown`。
- [ ] `finally` 中审计失败不得覆盖原始业务异常。
- [ ] 明确记录 `succeeded`、`failed`、`cancelled`、`timeout`。
- [ ] 取消生成、客户端断开、超时均不得记录为 succeeded。
- [ ] 检查 master orchestrator 是否绕过 Gateway；若绕过，统一经过带 timeout 和审计的调用封装。

**验收命令**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_stream_audit_failure.py
```

## Task 4: 修复 AI 助手 quota 退款

**Files**

- Modify: `app/services/ai_assistant.py`
- Modify: `app/core/redis.py`
- Test: `tests/test_ai_assistant_quota_refund.py`

**实施步骤**

- [ ] 覆盖 `assistant_message`、`polish_profile`、`parse_search`、`match_page` 四条调用链。
- [ ] provider 调用失败、超时、解析失败时执行退款。
- [ ] 成功请求只扣一次 quota。
- [ ] `assistant_message` 的用户消息在未提交时发生失败，必须 rollback。
- [ ] 退款异常单独记录，不得替换原始异常。
- [ ] `match_page` 的多候选调用必须明确一次请求的 quota 计费单位。

**验收命令**

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_ai_assistant_quota_refund.py
```

## Task 5: 实现 Redis 原子退款

**Files**

- Modify: `app/core/redis.py`
- Test: `tests/test_redis_refund_atomic.py`

**实施步骤**

- [ ] 使用 Lua 脚本将读取、判断、递减合并为原子操作。
- [ ] count 小于等于 0 时不得继续递减。
- [ ] key 不存在时返回明确结果。
- [ ] 保留原 key TTL。
- [ ] Redis 不可用时返回失败，不得伪造退款成功。
- [ ] 增加并发退款测试，验证最终值不会为负数。

## Task 6: 修正 AI 军师日志状态

**Files**

- Modify: `app/services/ai_advisor.py`
- Test: `tests/test_ai_advisor_audit_status.py`

**规则**

- 风控拦截记录 `blocked`。
- 业务异常、provider 异常、数据库异常记录 `failed`。
- 正常完成记录 `success`。
- 错误原因通过 `error_code`、HTTP status 或 metadata 表达。
- 不新增 `error` 状态，除非数据库和 API 文档同步完成。

# 批次二：契约、状态模型和生命周期

## Task 7: 统一幂等键校验

**Files**

- Modify: `app/api/routes/ai_memory.py`
- Modify: `app/services/ai/memory/service.py`
- Modify: `docs/api/AI记忆.md`
- Test: `tests/test_ai_memory_idempotency.py`

**规则**

- 公开接口继续接受 1–128 字符。
- 统一处理空字符串、空白字符和非法字符。
- 内部追加 `:claim` 后，最终 key 仍必须不超过 128 字符。
- confirm、correct、suppress、lift 使用同一校验逻辑。
- 超限返回稳定 4xx 错误。

## Task 8: 定义旅程状态转换表

**Files**

- Modify: `app/services/ai/moxiang_state.py`
- Modify: `app/services/ai/journey.py`
- Modify: `docs/ai-journey-state-machine.md`
- Test: `tests/test_ai_journey_state_machine.py`

**必须先定义**

- 发布状态是否单调前进。
- snooze、暂停、重新唤醒属于哪类交互状态。
- `building → chatting` 是否为合法转换。
- 并发更新使用何种 revision 检查。
- 非法转换返回什么错误。

**实施要求**

- 不得对所有状态更新机械调用单调推进函数。
- 既有 snooze 流程必须保持可用。
- 每个合法转换和非法转换都要有测试。

## Task 9: 明确 transcript 和 outbox 生命周期

**Files**

- Modify: `app/core/config.py`
- Modify: `app/services/ai/memory/derivations.py`
- Modify: `app/workers/ai_worker.py`
- Modify: `docs/ai-memory-phase2-rollout-20260905.md`
- Test: `tests/integration/ai/test_retention_and_outbox.py`

**实施要求**

- 分别定义音频、transcript、memory state、outbox、审计数据的 retention。
- 保留 `memory_projection` 现有内部通知契约。
- 为 outbox 定义成功保留期、失败重试、死信和清理规则。
- 只有消费者和文档完成迁移后，才可删除事件类型。
- 用户删除请求必须明确是否删除 transcript、审计和备份副本。

## Task 10: 统一会员权限实现

**Files**

- Inspect and reuse: `app/services/membership.py`
- Modify: `app/services/ai_advisor.py`
- Modify: `app/services/ai_assistant.py`
- Test: `tests/test_vip_authorization_consistency.py`

**实施要求**

- 优先复用已有 membership helper。
- 最终只保留一个权限判断来源。
- 覆盖普通用户、有效 VIP、过期 VIP、并发状态变化。
- 不把 `viewer_vip` 误判为权限绕过。

# 批次三：性能和异步架构

## Task 11: WebSocket 改为 commit 后通知

**Files**

- Modify: `app/services/ai/tasks.py`
- Modify: `app/workers/ai_worker.py`
- Modify: `app/api/routes/voice_moxiang.py`
- Test: `tests/test_task_websocket_notifications.py`

**实施要求**

- 数据库事务成功 commit 后再 publish。
- 覆盖 succeeded、failed、cancelled、superseded。
- WebSocket 建立时先读取一次最终任务状态，解决订阅竞态。
- Redis 故障自动退回轮询。
- disconnect 时 unsubscribe 并关闭资源。
- 不在 `complete_task()` 尚未提交时发布完成事件。

## Task 12: 实现 NLS Token 安全缓存

**Files**

- Modify: `app/services/voice/stream_provider.py`
- Modify: `app/services/voice/stream_tts_provider.py`
- Modify: `app/services/voice/providers.py`
- Test: `tests/test_nls_token_cache.py`

**缓存键组成**

- access key identity
- secret 的不可逆指纹
- region

**必须处理**

- token 过期前刷新。
- credential rotation。
- 多事件循环并发刷新。
- 测试环境缓存隔离。
- 不将 secret 明文写入 Redis key。

## Task 13: 优化 Persona 缓存失效

**Files**

- Modify: `app/services/ai/memory/consumers.py`
- Test: `tests/test_persona_cache_invalidation.py`

**实施要求**

- 以索引集合或 generation/version key 替代逐键 SCAN。
- 写入成员和索引时处理竞态。
- 处理 index TTL 早于成员 key。
- 清理失效成员。
- 避免对超大集合执行无界 `SMEMBERS`。
- 增加写入、撤销、并发失效测试。

## Task 14: 修复 Search/Recommend correctness 后再批量化

**Files**

- Modify: `app/services/ai/search.py`
- Modify: `app/services/ai/recommend.py`
- Test: `tests/test_ai_search_memory_projection.py`
- Test: `tests/test_ai_recommend_projection.py`

**实施顺序**

- [ ] 先确认 memory mode 的 `source_revision` 和 consent 数据是否完整。
- [ ] 修复或明确 `_candidate_projection_is_current()` 的业务语义。
- [ ] 增加 fail closed、可见性、consent、revision 回归测试。
- [ ] correctness 通过后，再将逐用户 `read_active()` 改为批量读取。
- [ ] 保持排序、分页、空结果和部分结果语义不变。

## Task 15: 批量物化 Search/Recommend 结果

**Files**

- Modify: 相关 search/recommend materialization service
- Test: `tests/integration/test_bulk_materialization.py`

**实施要求**

- 使用批量 INSERT。
- 保留 ON DUPLICATE KEY 语义。
- 保留 full/partial 结果。
- 保留 generation/version。
- 单事务失败时整体 rollback。
- 重试不得产生重复或旧版本覆盖新版本。

# 批次四：低风险重构和运营化

## Task 16: 审计写入连接与背压

**Files**

- Modify: `app/services/ai/audit.py`
- Modify: `app/core/config.py`
- Modify: `app/workers/ai_worker.py`
- Test: `tests/test_audit_backpressure.py`

**二选一实现**

- 独立 `mysql+pymysql` 连接池；或
- 有界异步审计队列和专用 AsyncSession。

必须明确：

- 队列满时如何处理。
- 审计故障是否阻塞主链路。
- worker 关闭时如何排空。
- 丢失审计如何告警和补偿。
- 连接池上限、连接超时和重试次数。

## Task 17: 统一清理、指标和运行手册

**Files**

- Modify: `app/workers/ai_worker.py`
- Modify: `docs/DEVELOPMENT.md`
- Create: `docs/runbooks/ai-retention-and-recovery.md`

**必须输出指标**

- 清理文件数量。
- 清理失败数量。
- 清理耗时。
- outbox backlog。
- 审计丢失数量。
- WebSocket fallback 次数。
- provider timeout 次数。
- quota refund failure 次数。

运行手册必须包含：

- 配置项。
- 告警阈值。
- 手工重试命令。
- 回滚步骤。
- 数据删除和恢复步骤。

# 每批次验收流程

每个任务都按以下顺序执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q <focused-test>
.\.venv\Scripts\python.exe -m pytest -q <related-test-group>
```

批次完成后再执行：

1. MySQL 集成测试。
2. Redis 集成测试。
3. Worker 实际运行验证。
4. Provider mock/故障回放。
5. WebSocket 连接、断开、重连验证。
6. 性能基线和优化后对比。
7. 记录变更文件、契约影响、测试结果、运行结果和回滚方法。

不得使用"测试通过"代替生产验收。生产发布前必须单独取得真实运行环境、日志、指标和客户端链路证据。

# 交付顺序

1. 批次一全部通过后，才进入批次二。
2. 批次二的状态、生命周期和幂等契约冻结后，才进入批次三。
3. 批次三每个性能改动独立发布、独立压测。
4. 批次四只处理已经稳定功能的重构和运营化。
5. 每个任务完成后保留一个可回滚提交。

# 默认假设

- 保持当前公开 API 兼容。
- 不新增业务能力，只修复缺陷、补齐生命周期和降低资源开销。
- 生产环境暂不假设存在同步 SQLAlchemy engine。
- quota 退款必须具备幂等性。
- 所有异步通知必须以数据库 commit 成功为前提。
- 未取得生产证据前，验收状态只能写"源码/测试/集成/运行验证"，不能写"生产通过"。
