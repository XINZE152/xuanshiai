# AI Memory Kernel Phase 3 上线与验证记录（2026-09-06）

> 计划：`docs/superpowers/plans/2026-09-06-memory-kernel-phase3-consumers-luna.md`
> 分支：`codex/memory-kernel-phase3-consumers`（自 bd2be00 创建；前置 Core v1
> 77645b1、Phase 2 bd2be00、Phase 2 审查收口 a5c01b0 均可追溯）。

## 1. 交付范围与源码清单

新增：

| 文件 | 内容 |
| --- | --- |
| `app/services/ai/memory/consent_producers.py` | 授权生产者：consent→8 维度 ProjectionGrant 授予/撤销、失败安全重试（outbox）、user_deleted/account_deleted 链 |
| `app/services/ai/memory/consumers.py` | 消费者适配：CounselorMemoryAdapter（军师）、PersonaMemoryAdapter（分身）、SanitizedMemoryContext、公开字段 allowlist、缓存 |
| `app/api/routes/ai_memory.py`（修改） | GET `/ai/memory/view`、POST `/ai/memory/grants/{grant_id}/revoke` |
| `app/services/ai/memory/service.py`（修改） | list_memory_view_items / list_projection_grants / revoke_projection_grant |
| `docs/api/ai-memory-consumers.md` | 新端点完整契约（PROJECT_RULES 模板） |
| `migrations/ai/20260906_01_memory_projection_id_widen_{up,down}.sql` | projection_id/grant_id 列宽 64→96 |
| 测试 | `tests/test_ai_memory_consent_producers.py`(18)、`tests/test_ai_memory_consumers.py`(18)、`tests/test_ai_memory_view_routes.py`(9)、`tests/integration/ai/test_ai_memory_phase3_real_db.py`(6) |

修改要点：

- `consents.py`：grant_consent / revoke_consent 同事务调用生产者（失败不影响
  consent 状态：安全日志 + `memory_projection_producer` outbox 重试）。
- `profile.py`：delete_ai_profile 撤销 consent 后同步撤销投影授权。
- `schemas/ai_memory_projection.py` + `projection_policy.py`：启用
  `counselor_context` / `persona_context`（枚举 + PROJECTION_FUNCTIONS；
  PROJECTION_FUTURE_FUNCTIONS 置空；future_consumer_spec 改为按注册表门控）。
- `ai_worker.py`：`_run_cleanup_round` 显式加载 memory 派生与生产者 handler
  注册（修复 Phase 2 隐性缺口：独立 worker 进程此前会把 memory_* 事件
  dead-letter）。

## 2. 授权生产者语义（Task 1）

- 授予：`profile_text_extract` consent 成功提交后，服务端从 consent 行派生
  snapshot（`derive_consent_snapshot_id`），为 8 个消费维度 upsert grant：
  search/candidate_filter/personal_profile、compatibility/candidate_rank/
  compatibility_features、recommend/candidate_rank/ideal_partner_preference、
  counselor_context/{session_context,explanation}/{personal_profile,
  ideal_partner_preference}、persona_context/session_context/
  public_profile_summary。
- 撤销：公开 revoke、画像删除、账号注销（user_deleted/account_deleted 链）
  同步撤销全部授权并立即失效 active 投影；读端另有 consent/snapshot/policy
  复核门双保险。
- 幂等键：`consent:{owner}:{scope}:{snapshot_id}:{action}`（哈希进重试事件
  event_id）；快照版本变化（重授/过期）后旧引用一律拒绝。
- 手动单维撤权（新端点）递增 privacy revision，使挂起的旧 revision 向量
  生产者重试按 superseded 收口，不复活已撤销授权。

## 3. 历史测试证据（2026-09-06 代码快照）

> 以下结果只覆盖 commit `7aa159a` 前的 Phase 3 实现。2026-09-07 对军师实际
> 接入、缓存门控与 API 契约的改动必须在本次交付收口中重新验证，不能据此宣称通过。

### 3.1 单测（fake session，hermetic）

- 全量：**1283 passed**，4 failed = 已知预存红（2×moxiang_archive 在干净
  bd2be00 基线复现确认；2×journey_feature_gate 为本地 .env 污染 Settings，
  与本计划无关），14 skipped，1 xfailed。
- 新增：consent_producers 18 + consumers 18 + view routes 9 = 45 全绿。
- 更新既有断言：future-key fail-closed → enabled（policy/schemas/
  projections 三处测试），Phase 2 rebuild 计数 1→8（维度全集）。

### 3.2 真实 DB 集成（dedicated MySQL 3307 + Redis 6380）

`tests/integration/ai/test_ai_memory_phase3_real_db.py`：6 passed

1. consent 授予 → 8 维度 grant 落库（真实行校验）。
2. 闭环：consent → grant → build → 军师上下文（含双主体条目）→ 分身公开
   上下文（公开 allowlist 过滤）→ revoke → 全部 grant revoked + 投影
   invalidated → 双适配器读取 fail closed。
3. 生产者失败注入 → `memory_projection_producer` 重试事件落库 → handler
   重放后 8 授权 active。
4-6. legacy/shadow/memory 三模式：legacy/shadow 不修改 Core 事件/Claim 行；
   memory 模式授权驱动可读且 Core 仅追加（append-only）。

记忆域其余集成：test_ai_memory_projection_real_db / test_ai_memory_real_db /
test_ai_consent_real_db / test_ai_profile_real_db 共 32 passed。

### 3.3 Provider 输入脱敏证据

- 单测 + 集成均断言 prompt payload：无 `source_quote` / `transcript` /
  `evidence_ref` / `source_kind` 字段；raw 摘录文本（"对话原文：…"句式）
  不出现在序列化载荷；分身载荷无 ideal_partner 主体与非公开自由文本。
- Mock Provider 集成：录制式 Provider 收到的消息不含原文（单测钉死）。
- 敏感日志扫描：caplog 全级别扫描适配器执行路径，无字段值/原文落日志。

### 3.4 缓存证据

- 键：`ai:memory:persona:v1:{viewer}:{target}:{projection_version}:{privacy_revision}`，
  TTL 300s；不同 viewer 隔离、同 viewer 命中、形状校验失败按 miss。
- 撤权/注销主动失效：`invalidate_persona_memory_cache`（SCAN+DELETE），
  并接入 `revoke_projection_dimensions_for_owner`；Redis 不可用时 best-effort
  跳过（键内 revision 变化 + TTL 兜底收敛）。

## 4. 迁移与回滚

- `20260906_01_memory_projection_id_widen`（真实 DB 暴露：counselor/persona
  维度组合 id 最长 84 字符，超 varchar(64)，写入 1406）。已登记 manifest
  （sha256 校验），`--target test up` succeeded。
- 回滚演练：`down` → 全链 13 个迁移 rolled_back；`up` → 全链 succeeded。
- down 防护：存在 >64 字符行时 down 以 1265 Data truncated 响亮失败
  （fail-safe，不静默截断）；清空超长行后 down 成功。
- 读取模式回滚（legacy/shadow/memory）见 §3.2-4：切回 legacy 不改 Core 行。

## 5. 2026-09-07 本次交付验证

- 后端 hermetic 回归：
  `uv run pytest tests/test_ai_memory_ledger.py tests/test_ai_memory_routes.py
  tests/test_ai_memory_projections.py tests/test_ai_memory_consent_producers.py
  tests/test_ai_memory_view_routes.py tests/test_ai_memory_consumers.py
  tests/test_ai_advisor.py tests/test_ai_avatar.py
  tests/test_verify_memory_real_provider.py tests/test_dots_provider.py -q`，结果
  **139 passed**。
- 静态质量：变更路径 Ruff 通过；`python -m compileall` 通过；`git diff --check`
  通过。
- 临时 FastAPI 进程的 `/openapi.json` 已包含
  `POST /api/v1/ai/avatar/{target_user_id}/reply`；未鉴权 POST 实测返回 401，说明
  路由已在运行进程暴露且仍受认证保护。
- 小程序源码契约：`node tests/test-memory-management-contract.js` 与
  `node tests/test-mock-system.js` 均通过。前者覆盖记忆查看/确认/纠正/停止使用/
  撤回授权、双主体、revision、幂等键、运行时 Mock 无持久化，以及分身真实模式
  必须调用后端。

## 6. 本次完成的消费者接入

- `app/services/ai_advisor.py` 已接入 `CounselorMemoryAdapter`：`legacy` 的
  Provider 输入保持原样，`shadow` 只记录脱敏统计，`memory` 仅在当前用户已确认、
  active 且授权有效的 `session_context` 投影存在时送入消毒载荷。任何授权、策略
  或投影异常都拒绝 memory 请求，不扣额度也不调用 Provider；raw event、
  transcript 和 `source_quote` 永不进入 Provider。
- 已新增受认证保护的 AI 分身回复端点。它只通过 `PersonaMemoryAdapter` 取得公开
  allowlist 上下文；无上下文时以 404 收口且不消耗额度，撤权后缓存不能绕过
  privacy revision 的实时校验。Provider 回复在返回前还要经本地内容过滤与
  冒充本人、关系承诺、联系方式、超长文本的确定性边界审查；违规以 422 拒绝并退还
  额度。服务不持久化会话，也不接受用户代答、转交本人或透露隐私的指令。
- 微信端记忆管理页已接到档案页；AI 分身在真实模式调用上述服务端端点，Mock
  模式才保留本地演示回复；真实分身回复只在当前页面内存中渲染，不写入本地聊天
  存储。

## 7. 尚待外部环境完成的真实验证

> **2026-09-07 补记**：本节前两项已完成，结果如下。
>
> - 真实 DB 集成（已完成）：Docker Engine 稳定后用 `compose.ai-test.yml` 仅启动
>   mysql/redis，全新库执行基础 schema 初始化 + `manage_ai_migration.py up
>   --target test`（13 个版本全部应用，verified=current）。
>   `test_ai_memory_phase3_real_db.py` 6/6 通过，`test_ai_memory_real_db.py` +
>   `test_ai_memory_projection_real_db.py` 19/19 通过。过程中修复两处：
>   ① phase3 读取模式用例的手工 setup 只 build 了 personal 类别，与军师
>   「两个冻结类别都过授权」契约不符（生产链路由
>   `rebuild_dimensions_for_owner` 物化全部已授权维度），已对齐；
>   ② `scripts/backfill_ai_memory.py` 的 candidate 与 revision_field 两个
>   扫描循环共用同一游标，revision_field 表低 id 行会被整段跳过（丢数据），
>   已改为各表独立游标（单测 20 过 + 真实 DB 回归钉住）。
> - 微信构建（编译已完成，工具回归待执行）：HBuilderX CLI 编译成功
>   （36s，产物 `unpackage/dist/dev/mp-weixin` 2026-09-07 15:55，66 页面
>   齐全，含 MemoryManagementSheet）。`npm run verify:mp:dev` PASS；
>   `npm run verify:mp`（发布 2MiB 阈值）主包 2706KiB 超限为 dev 产物与
>   并行工作线的预存问题（本任务产物贡献仅约 57KiB），发行模式构建另行
>   验证。微信开发者工具导入回归与真机验收仍待执行。
> - 真实模型：Dots 探测同日复跑 PASS（dots3-note-prev，11.7 秒，1 条建议，
>   risk_level=low，消毒载荷校验通过）。

- 真实 DB 集成：本机 `127.0.0.1:3307` 拒绝连接，且 Docker Engine 未就绪，故
  `tests/integration/ai/test_ai_memory_phase3_real_db.py` 的 6 个用例在 fixture
  建库前报连接错误；这不是代码断言失败，不能标记为通过。（2026-09-07 已解决，
  见上方补记）
- 真实模型：Dots 探测脚本
  `uv run python scripts/verify_memory_real_provider.py --allow-real-provider --provider dots`
  已通过。实际调用 `dots3-note-prev` 耗时 18.6 秒，返回可通过军师 JSON 契约的
  1 条建议、`risk_level=low`；记录仅包含响应字节数和 SHA-256，未记录模型原文、
  endpoint、密钥或用户资料。脚本拒绝生产环境，只发送合成、无个人信息的消毒记忆
  上下文。
- 微信构建与真机：本机可用 HBuilderX `5.15.2026070915`，且已导入当前项目；但
  `launch mp-weixin --project xuanshiai-vue --compile true` 连续等待一分钟没有
  编译输出，产物仍停留在 2026-09-06 18:52，说明 CLI 运行时阻塞而非源码编译通过。
  根目录 npm 构建会因 UniApp X 工程布局查找不存在的 `src/manifest.json` 失败，不能
  替代 HBuilderX。因而微信开发者工具导入和真机回归仍未执行。
- 生产监控面板、跨用户行为学习、分身转交本人流程仍不属于本次范围。

## 8. 并行工作说明

同一工作区存在另一条进行中的工作线（墨相师叙事与发布消费闭环，
`docs/superpowers/plans/2026-09-06-moxiang-narrative-projection-closure.md`），
其 `profile_projection_handler` source_revision 固定逻辑改动
（app/services/ai/profile.py 局部 hunk、test_ai_trilogy_e2e.py）尚未完成
（其计划 Task 2 一项未勾选）。已验证：stash 该线改动后，本阶段全部相关
测试（32 集成 + 1283 单测）通过；该线改动引起的
`test_real_publish_pins_revision_consent_and_projection` 失败归属其进行中
工作，不随本阶段提交。
