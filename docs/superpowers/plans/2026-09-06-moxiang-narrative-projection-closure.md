# 墨相师叙事与发布消费闭环完善实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 固化现有画像叙事接口，并补齐确认发布后画像投影进入搜索与匹配消费的可重复验收证据。

**Architecture:** 保留现有 `profile_narrative` Worker 和统一 REST 接口，不改变公开状态机。以现有真实 MySQL/Redis 集成为基础，扩展发布任务完成、投影生效、搜索和匹配读取的闭环测试；若测试暴露状态或任务编排缺陷，只做最小修复。

**Tech Stack:** FastAPI、SQLAlchemy async、MySQL 8、Redis 7、pytest、UniApp UTS。

## Global Constraints

- 保留两个仓库的脏改动，不 stash/reset/clean/commit/push。
- 不修改受保护的前端配置、路由清单或生产密钥。
- 已确认并发布的 personal 画像才能进入 searchable/compatibility 投影；ideal_partner 只能作为本人偏好消费。
- narrative 是展示性内容，不进入搜索、匹配或兼容度计算。
- 本轮优先验证现有契约，不新增重复接口。

### Task 1: 固化 narrative 契约与状态边界

- [x] 增加或补强 pending、pending_confirmation、confirmed 三态和 subject 隔离测试。
- [x] 运行画像叙事 focused tests。
- [x] 确认现有接口契约与文档一致，本轮无需新增接口。

### Task 2: 补齐发布到搜索/匹配的真实数据库闭环

- [x] 移除闭环测试对 `ai_profile_projection_status` 的人工 seed 依赖。
- [ ] 运行真实数据库测试确认 Worker 自动写入 active 准入位。
- [x] 补充叙事、投影、搜索、匹配读取断言。

### Task 3: 前端 narrative 结果页契约回归

- [x] 校验真实 narrative GET、pending、确认、失败重试和 regenerate 调用。
- [x] 运行前端墨相师契约测试和 diff 检查。

### Task 4: 分层验证

- [x] 运行后端 focused unit tests。
- [x] 运行前端墨相师契约测试。
- [x] 检查双仓库 diff，并记录真实数据库、Worker、搜索、匹配和微信端的验证边界。
