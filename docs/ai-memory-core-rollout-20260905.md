# 记忆内核核心 v1 交付说明（2026-09-05）

> 对应执行计划 `docs/superpowers/plans/2026-09-05-memory-kernel-core-luna.md`；
> 产品定义见 `PRODUCT.md` §「记忆内核核心 v1」；架构契约见
> `docs/architecture/ai-memory-kernel-core-v1.md`；API 文档见 `docs/api/ai-memory.md`。

## 1. 交付状态总览（按层区分，不得混读）

| 层 | 状态 | 说明 |
| --- | --- | --- |
| 代码 | 完成 | 记忆内核（Ledger/Materializer/Policy/Service/Derivations）、墨相师 Shadow Write、确认/纠正/删除/解除 API、旧动作转发、回填脚本 |
| 迁移 | 完成（test 库已验证） | `migrations/ai/20260905_01_memory_kernel_core_{up,down}.sql`，manifest 已登记，`manage_ai_migration.py` verify 已扩展；test 专用 MySQL 已 `up` 成功 |
| 单元测试 | 完成 | 10 个新测试文件 + 既有回归全绿 |
| 真实 DB 测试 | 完成 | 专用 MySQL（3307）上 11 个用例：Shadow Write、subject 隔离、assertion_mode 映射、subject 不匹配拒绝、写入失败隔离、server_seq 连续、并发 append、重复确认回放、State TTL、草稿确认转发、回填断点续跑 |
| Mock Provider | 已使用 | 集成测试用确定性 stub/Mock Provider，未调用真实 LLM |
| Worker | 部分 | in-process `_run_round` 验证；`--consumers` 消费循环通过 `register_cleanup_handler` 注册复用（未做容器级演练） |

**重要口径：以上均为分支 `codex/memory-kernel-v1` 上的测试通过（Shadow Write PASS），
不等于"生产已切换"。生产未发生任何读取路径变化。**

## 2. 代码范围

- 新增：`app/schemas/ai_memory.py`；`app/services/ai/memory/{__init__,ledger,materializer,policy,service,derivations}.py`；
  `app/api/routes/ai_memory.py`；`scripts/backfill_ai_memory.py`。
- 修改：`app/services/ai/journey.py`（候选 upsert 后 Shadow Write）、
  `app/services/ai/profile.py`（草稿 confirm/replace/reject/delete 结果转发记忆）、
  `app/services/ai/base.py`（抽取 Schema 增加 `assertion_mode`，缺省 inferred）、
  `app/services/ai/prompts/profile_extract.py`（master 提示词要求显式 assertion_mode）、
  `app/api/router.py`（注册 `/ai/memory` 路由）、
  `scripts/manage_ai_migration.py`（verify 增加 20260905_01 门控）、
  `migrations/ai/manifest.json`（登记新版本）。
- 抽取契约：`assertion_mode ∈ {explicit, inferred}` 由模型显式给出，
  Shadow Write 据此映射 `user_explicit/inferred`，**绝不从 confidence 反推**；
  历史 candidate（无 assertion_mode）回填时按固定 legacy 规则映射为 `inferred`。

## 3. 数据与隐私边界

- `ai_memory_event` append-only（无 UPDATE/DELETE 路径）；纠正/替换/删除/解除全部生成新事件并保留 `causal_event_ids_json`。
- 只保存最小 `source_quote`（≤512 字符）/`source_ref`；事件 payload、outbox payload、日志、API 响应均不含 transcript。
- 日志纪律：Shadow Write 失败只记录异常类型名与 ID；subject/policy 违规用可检索标记 `memory_shadow_policy_denied` / `memory_forward_policy_denied`，消息不含用户原话。
- 不新增 consent scope / 授权表；授权继续复用 `profile_text_extract`。

## 4. 测试与验证

单元 + 集成共 10 个新测试文件；既有回归（publish/projection/sessions/master_prompt/tasks/ai_tasks）全绿。
真实 DB（专用 MySQL，127.0.0.1:3307）验证项：

1. Shadow Write 主链路与 outbox 入队（同一 event 只入队一次）；
2. subject 隔离与 fact_kind 互锁；供应商主体不匹配 → 整轮拒绝、零记忆；
3. assertion_mode 决定来源（inferred 高置信也只能 proposed）；
4. 记忆写入失败不阻塞旧候选链路；
5. owner 内 `server_seq` 连续唯一（含 6 路并发 append）；
6. 重复确认：同 Idempotency-Key 回放同一事件；过期 revision 稳定 409；
7. 纠正保留因果链；草稿确认转发使记忆 Claim confirmed；
8. State TTL 到期幂等过期；
9. 回填：candidate → proposed Observation、confirmed revision → confirmed Claim、
   未知 subject/dimension 跳过计数、`legacy:<table>:<pk>` 断点续跑、dry-run 不写。

## 5. 未实施项（延期，另立项）

- Search / Compatibility / Recommend 切换读取 Memory Projection——**未切换，仍读旧投影**；
- Projection / ProjectionGrant 与 granular consent；
- 前端 Memory View；
- AI 军师 / AI 分身 namespace；
- 真实用户行为学习（behavior 来源本期不自动产生）；
- `--consumers` 容器级消费演练与生产灰度编排。

## 6. 风险与运维提示

- 上线顺序：先 `manage_ai_migration.py up --target development`（建 7 张空表，纯新增无破坏），再发布代码；Shadow Write 在旧链路之外，代码回滚不需要 down 迁移。
- 回填脚本幂等可重跑（`--resume-from` / dry-run）；对生产执行前先在 test 库演练（已通过）。
- 消费侧复用 cleanup 消费者；`ai_worker --consumers` 进程需在代码发布后重启以加载记忆 handler 注册。
- 集成测试环境注意：本地若运行 compose 中的 worker-a/worker-b 容器，会与进程内 `_run_round` 抢任务（本次曾因此出现任务被外部租约拿走），跑集成测试前先 `docker compose -f compose.ai-test.yml stop worker-a worker-b`。

## 7. 计划文件边界偏差登记

以下文件不在计划 §3 文件清单内，但为交付必要组成，特此登记：

1. `migrations/ai/manifest.json` —— 不登记则 up 迁移永远不会被执行；
2. `scripts/manage_ai_migration.py` —— verify 需为 20260905_01 增加门控校验块（沿用 Task 10 模式）；
3. `app/api/router.py` —— 一行注册，否则 5 个新端点不存在；
4. `app/services/ai/base.py` —— Task 5 步骤 3 明确要求的"对应抽取 Schema"；
5. `tests/integration/ai/conftest.py` —— sweep 清扫清单补 7 张记忆表（否则集成测试跨用例数据残留）。

## 8. 对抗性审查与修复（2026-09-05 交付后复审）

对已完成的 Task 0–9 做对抗性审查，发现并修复 4 类问题（均有回归测试钉死）：

1. **P0——API 写端点缺 commit（已修复）**：`ai_memory.py` 四个 POST 端点此前没有任何
   `await db.commit()`，而 `get_db` 只负责关闭会话（未提交即回滚）——确认/纠正/删除
   在生产上会返回 200 但静默丢失。兄弟路由（ai_search/ai_moxiang）均为显式 commit
   模式。修复 + 路由测试断言 `session.commits`。
2. **P1——删除后列表仍显示（已修复）**：`suppress_claim` 只生成墓碑不改 Claim 行，
   `list_memory_items` 未过滤活动墓碑，"删除"对用户无可见效果。修复：列表 SQL 增加
   `NOT EXISTS` 活动墓碑过滤；解除墓碑后恢复展示。API 契约文档已回写。
3. **P1——锁序倒置死锁窗口（已修复）**：confirm/correct/suppress_claim 先锁 claim 行
   再进 append 锁 owner 行，与 propose（owner 先行）构成 AB-BA；insight 失效级联同病。
   修复：所有 MemoryService 变更入口与级联统一 `lock_owner()` 先行。重放路径不受影响。
4. **P2——owner 首写竞态死锁（已修复）**：`_lock_owner_sequence` 旧实现"先 SELECT FOR
   UPDATE 再 insert-if-missing"，REPEATABLE READ 下真并发首写互持间隙锁，裸连接实验
   实测 1213 死锁。修复：ensure-first（幂等 upsert 先行），后到事务阻塞在 duplicate-key
   检查上按行锁串行。裸实验验证并发首写序号连续唯一。注意 pytest 建连节奏可能错开
   竞态，锁序由语句顺序单测确定性钉死。
5. **P2——物化器并发冲突防御（已修复）**：claim/suppression INSERT 命中唯一键冲突时
   由"抛 IntegrityError"降级为"回读让位"（first-wins，事件保留在账本），消除竞态败者
   在调用方事务内留下半套物化行的窗口。

已知未接线项（本次登记，另立项）：`expire_memory_states` 已实现并有测试，但 v1 无
任何 State 生产者、也无周期调度入口——State 生产落地时需同步接入 worker 周期清理。
