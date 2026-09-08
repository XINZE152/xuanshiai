# Memory Projection Phase 2 交付与回滚说明（2026-09-05）

> 计划：docs/superpowers/plans/2026-09-05-memory-kernel-phase2-projection-luna.md
> 基线：Memory Kernel Core v1（commit 77645b1，分支 codex/memory-kernel-v1）
> 分支：codex/memory-kernel-phase2-projection（自 77645b1 建立）

## 1. 交付状态（按层）

| 层 | 状态 | 说明 |
| --- | --- | --- |
| Schema / Policy / allowlist | 完成 | 冻结词汇表（function×purpose×category）、10 字段 entry allowlist、subject↔category 互锁、input hash；future key fail closed |
| 数据库迁移 | 完成 | 20260905_02：ai_memory_projection_grant（维度唯一+revoke 列）+ ai_memory_projection（版本唯一+input hash 唯一+invalidated 列）；down 逆序仅删本阶段两表 |
| Grant 服务 | 完成 | 服务端重读授权快照（cs_ 前缀派生 id）、policy revision 三方一致校验、重复 grant 幂等、revoke 立即失效维度全部 active 投影、非 owner revoke 404 |
| Builder / 版本 / Outbox | 完成 | 只收 confirmed Claim（活动墓碑事实排除）；field_key 反解（structured 白名单 / entry 类别重算 hash）；版本单调 + 同 hash 幂等；同内容历史版本接管激活；outbox 以 projection:{owner}:{function}:{purpose}:{category}:{input_hash} 幂等 |
| Shadow / 三态开关 | 完成 | AI_MEMORY_PROJECTION_READ_MODE=legacy(默认)/shadow/memory（Literal 启动校验 fail fast）；canonical diff 只比较 subject/field key/value type/status/source kind/claim id，日志只含 hash/计数/字段 key/差异类型 |
| Search 接入 | 完成 | _load_projections 三态分发；memory 模式 entries→fields dict，ideal_partner 投影结构性不可达，缺投影候选人不进入结果 |
| Compatibility / Recommend 接入 | 完成 | 双方 FeatureSet 来自 compatibility_features + ideal_partner_preference（仅本人 preference input）；候选池发现按维度投影 owner；未确认 importance 中性化 0.5、constraint_type 投影边界清空 |
| Future key 登记 | 完成 | counselor_context/persona_context 登记最小输入与隐私门槛，全部调用 FEATURE_NOT_ENABLED，零副作用（docs/ai-memory-future-consumers.md） |
| 真实 DB / 回滚演练 | 完成 | 专用 MySQL 3307：7 用例全过（见 §3） |

## 2. 测试证据

- 单元（计划测试 + 旧链路 5 文件）：**243 passed, 1 skipped**（快照条件跳过，预存）；
- 真实 MySQL + Redis + in-process worker 集成：**98 passed, 5 skipped**（语音 live 无密钥跳过，预存）；
  - 其中 Phase 2 真实 DB 7 用例：grant/build/revoke/read 闭环、并发同 hash build 唯一、
    outbox 幂等、suppress 后事实从投影消失、consent 撤回读取为 0、策略版本不一致 fail closed、
    模式切换不触碰 Core event/Claim；
- `python -m compileall app` 通过；`git diff --check` 干净；
- 隐私抽查：投影响应/日志/证据结构无 source_quote、transcript、字段原文（测试断言钉死）。

## 3. 模式矩阵与回滚演练（真实 DB 实测）

| 场景 | 结果 |
| --- | --- |
| legacy 读旧投影 | 行为不变（旧链路 119 用例基线全过） |
| shadow 双读 | 返回恒等于 legacy 结果；diff 日志只含计数/字段 key/差异类型 |
| memory 读记忆投影 | read_active 全门校验（owner/grant/consent/policy/status/版本） |
| memory 缺投影 | 按固定策略回退 legacy，fallback 计数日志 |
| 授权撤回 | 读取为 0（fail closed） |
| 策略版本不一致 | 读取为 0；恢复一致后读取恢复（不删数据、无需 DB 回滚） |
| 关闭开关（切回 legacy） | Core event/Claim 行数逐一对比无变化；投影数据保留 |

## 4. 未实施项（延期，另立项）

- 前端 Memory View；
- AI 军师 / AI 分身真实消费行为（仅登记边界，见 docs/ai-memory-future-consumers.md）；
- granular consent 产品流程；
- 真实 Provider、微信开发者工具、真机与正式放量；
- shadow 差异率的生产观测面板（本期仅结构化日志）。

## 5. 边界偏差登记

1. `migrations/ai/manifest.json` + `scripts/manage_ai_migration.py` —— 本地专用
   （.gitignore 约定，不进提交），但登记 20260905_02 版本与 verify 门控是迁移
   可执行/可校验的必要修改（沿用 Core v1 先例）；
2. `tests/integration/ai/conftest.py` —— sweep 清扫清单补 2 张投影表（否则跨用例残留）；
3. `app/services/ai/features.py` —— 计划未列 logger，但 shadow diff 日志需要
   （`logging.getLogger(__name__)`）；
4. `app/services/ai/memory/materializer.py` —— 计划列为修改文件，审查后确认无需
   改动（投影失效走 outbox 派生 + 视图状态更新，不涉及事件物化器）；
5. `MemoryProjectionService.build` 锁序 —— 计划未明确，真实 DB 并发测试暴露
   max_version 竞读（uk_ai_memory_projection_id 冲突），按 Core 锁序约定
   owner 序列锁先行修复。

## 6. 运维提示

- 上线顺序：`manage_ai_migration.py up`（建 2 张空表，纯新增无破坏）→ 发布代码；
  默认 read_mode=legacy，行为与现状完全一致；
- 切换 shadow → 观察结构化日志 `memory_projection_shadow_diff`（identical 比例/
  差异类型）→ 满足门槛后切 memory；回滚 = 改回 flag，无需数据库操作；
- worker 需重启以加载 `memory_projection` noop 消费者注册（防止未知事件类型报错）。

## 7. 对抗性审查补充（2026-09-06）

提交 bd2be00 后复审，发现并处置 5 项（1 项代码修复 + 4 项边界登记）：

1. **激活前提（重要）**：`MemoryProjectionService.grant` 当前没有任何生产调用方
   （读路径已全部接入，授权生产者是 Phase 3 范围）。因此**现在切到 memory 模式
   会 100% 回退 legacy**（fallback 日志可见），机制已就绪但惰性——这是诚实的
   fail-safe 而非故障。激活需要授权生产者（如 consent 授予时自动 grant 或
   管理路径），另立项。
2. **写入时契约校验（已修复）**：build 此前不跑 `ProjectionDocument` 校验，坏
   entries 会静默落库、读取端 fail closed 退化为回退（难排查）。修复：构建期
   校验，违规响亮失败（`ProjectionContractError`，消息只含计数），worker
   bounded retry 可见；旧 active 投影不受影响（fail-safe）。
3. **memory 模式有界陈旧窗口**：claim 确认 → outbox → worker 重建之间存在
   延迟，此窗口内 recommend/compatibility 读到的是"上一个有效版本"。方向保守
   （只可能缺最新确认事实，绝不含未确认数据），且随重建自愈。legacy 的
   revision 向量严格门在 memory 模式由"事件驱动重建 + active 门"替代，语义
   有意放宽，属已知取舍。
4. **memory 模式读取性能**：逐用户顺序读（search 每候选 ~3-5 条查询、
   compatibility/recommend 类似）。v1 规模可接受；量产前可批量化（JOIN 一次取
   多用户投影），不在本期做。
5. **表示力边界**：value 只支持 string/number/boolean/string_list——区间类
   结构化值（dict）不入投影，对应字段由规则引擎记 UNKNOWN（方向保守）；
   legacy 链路不受影响，shadow diff 会把这些字段记为 only_legacy（可观测）。

锁序补充说明：grant/revoke 只取 grant 行锁（grant → projection 行），全程不取
owner 序列锁；build/rebuild 取 owner 锁 → projection 行。无任何反向顺序路径，
无 AB-BA 死锁窗口（与 Core 锁序约定一致）。
