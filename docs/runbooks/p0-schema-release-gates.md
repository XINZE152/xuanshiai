# P0 发布前 Schema 门禁与迁移执行 Runbook（2026-11-09）

> 任务标识：`UT-2026-11-02-DOC-02-BE-20261109`
> 适用对象：测试服/生产发布同学。本文只描述 P0 的 Q23/Q45 迁移与 Q63/Q74 schema 校验；不改动业务数据。

## 0. 前置决策

| 决策 | 推荐值 | 未确认时的动作 |
| --- | --- | --- |
| D1 Q45 资料解锁价 | 50 | 不执行 Q45 迁移，只跑预检看红项 |
| D2 Q23 无 completion 行老用户 | 只回填 `data_complete_rate >= 100` 且未注销的用户 | 先跑审计 SQL，确认回填行数后再执行 |

审计 SQL（只读）：

```sql
SELECT COUNT(*) AS missing_rows
FROM users u
LEFT JOIN user_profile_completion c ON c.user_id = u.id
WHERE c.user_id IS NULL
  AND COALESCE(u.status, 1) <> 3
  AND COALESCE(u.data_complete_rate, 0) >= 100;
```

## 1. 发布前固定顺序

1. **备份**目标库（至少 `users`、`user_profile_completion`、`paper_plane_profile_unlock`）。
2. 在仓库根目录运行迁移布局/校验和门禁：
   ```bash
   python scripts/check_migrations.py
   ```
   期望：`migration check: OK ...`，退出码 0。
3. 执行 Q45 迁移（paper_plane 域当前没有自动 runner，按手工 SQL 执行）：
   ```bash
   mysql -h<host> -P<port> -u<user> -p <db> < migrations/paper_plane/20261106_01_unlock_cost_up.sql
   mysql -h<host> -P<port> -u<user> -p <db> < migrations/paper_plane/20261109_01_unlock_cost_default_up.sql
   ```
   然后执行两个 verify（返回 0 行即通过）：
   ```bash
   mysql ... < migrations/paper_plane/20261106_01_unlock_cost_verify.sql
   mysql ... < migrations/paper_plane/20261109_01_unlock_cost_default_verify.sql
   ```
   说明：`20261106_01` 的 up/verify 已修正为 `CHECK_CONSTRAINTS` + `TABLE_CONSTRAINTS` 联表；旧 SQL 在 MySQL 8 会报 1054。`20261109_01` 负责把默认值补到 50。
4. 执行 Q23 business 迁移：
   - 生产/测试若使用 `scripts/manage_business_migration.py`，该 runner 会按 manifest 顺序执行**全部** business 版本。确认历史版本均已执行后再用：
     ```bash
     BUSINESS_TEST_DATABASE_URL=... python scripts/manage_business_migration.py up --target test
     ```
   - 若只允许增量执行，直接执行新版本 up，再执行 verify：
     ```bash
     mysql ... < migrations/business/20261109_01_profile_completion_missing_rows_up.sql
     mysql ... < migrations/business/20261109_01_profile_completion_missing_rows_verify.sql
     ```
   回滚（仅撤销本次 backfill）：
   ```bash
   mysql ... < migrations/business/20261109_01_profile_completion_missing_rows_down.sql
   mysql ... < migrations/business/20261109_01_profile_completion_missing_rows_verify_previous.sql
   ```
5. Q63/Q74 的 schema 只校验、不重复建表（测试服 2026-11-07 已人工建表）：
   ```bash
   python scripts/check_release_schema.py --target test
   ```
   生产使用显式 URL（必须含 host 与 port）：
   ```bash
   python scripts/check_release_schema.py --database-url 'mysql://user:pass@host:3306/db' --allow-production
   ```
6. 预检四项全部 PASS 后再重启/启动服务：
   ```text
   PASS user_profile_completion.completion_grandfathered: present
   PASS table community_post_remind: present
   PASS table chat_message_hidden: present
   PASS paper_plane_profile_unlock.points_cost: default=50 check=(`points_cost` in (50,80))
   release schema check: OK checks=4 failed=0
   ```

## 2. 回滚边界

- **Q45**：`down` 只把默认值恢复为 80，**保留放宽后的 50/80 约束**。若库里已有 `points_cost=50` 记录，不能自动收窄为 80-only；需先导出并删除 50 分记录，再手工执行收窄。见 `docs/api/纸飞机资料解锁.md`。
- **Q23**：`down` 只删除 `algorithm_version='legacy-backfill-20261109'` 且 `completion_grandfathered=1` 的行；用户编辑后已被重算为 `profile-v5` 的行不会删除。
- **Q63**：`down` 会 drop `chat_message_hidden`，隐藏状态丢失，消息正文不受影响。
- **Q74**：`down` 会 drop `community_post_remind`，已发出的通知不回滚。

## 3. 失败处理

| 预检/迁移失败 | 含义 | 处理 |
| --- | --- | --- |
| `check_migrations.py` 非 0 | manifest/校验和/up-down 配对不一致 | 先修迁移文件与 manifest，不执行 SQL |
| Q45 verify 返回行 | 默认值不是 50，或约束不放行 50/80 | 按 up 重新执行；仍失败则人工核对 `INFORMATION_SCHEMA` |
| Q23 verify 返回行 | 仍有 100% 老用户缺 completion 行 | 检查 `users.status`/`data_complete_rate`，必要时重跑 up（幂等） |
| 预检 `missing` | 目标库未执行对应迁移/建表 | 按第 1 节顺序补齐，不要先启动服务 |
| 连接错误/退出码 2 | 配置或权限问题 | 检查 `DATABASE_URL`/`BUSINESS_TEST_DATABASE_URL`，不要在生产库上重试破坏性操作 |

## 4. 证据要求

每次执行必须留存：命令原文、完整 stdout/stderr、退出码；迁移额外留存 up/verify/down 的实际输出。缺少任一要素视为未完成。
