# 资料完整度 profile-v5 回填 Runbook（2026-11-10）

> 任务标识：`UT-2026-11-02-DOC-02-BE-PLAN-20261110-B`
>
> 适用：Q54 资料完整度累加模型的上线回填。无表结构变更，只重算数据。
>
> 脚本：`scripts/recalculate_profile_completion.py`

---

## 1. 前置条件

1. 代码已部署到目标环境，且 `COMPLETION_RULES` 为 27 项、合计 100。
2. 已确认 grandfather 策略：展示分按当前 27 项计算，`completion_grandfathered=1` 只豁免浏览/申请/聊天门禁。
3. 已备份目标库，备份文件离机保存：
   - `users`
   - `user_profile`
   - `user_partner_preference`
   - `user_auth`
   - `user_media`
   - `user_profile_completion`

## 2. 执行顺序（强制）

```text
备份 → （如生产尚未执行 Q23）先执行 20261109_01_profile_completion_missing_rows
     → 回填脚本 dry-run
     → 小批量 --limit 100
     → 全量
     → 再 dry-run 期望 changed=0
     → SQL 验证
```

> 为什么 Q23 必须先执行：Q23 会插入 `completion_grandfathered=1`、`score=100` 的存量行；如果先回填再补 Q23，这些新行会再次破坏恒等式。若顺序反了，Q23 完成后必须再跑一次回填（脚本幂等）。

## 3. 命令

### 3.1 测试服

```bash
# dry-run：只统计，不写库
.venv/bin/python scripts/recalculate_profile_completion.py --target test --dry-run

# 小批量演练
.venv/bin/python scripts/recalculate_profile_completion.py --target test --limit 100

# 全量
.venv/bin/python scripts/recalculate_profile_completion.py --target test

# 幂等复验：期望 changed=0
.venv/bin/python scripts/recalculate_profile_completion.py --target test --dry-run
```

`--target test` 读取 `BUSINESS_TEST_DATABASE_URL`，缺失时脚本拒绝回退到开发库。

### 3.2 生产

```bash
.venv/bin/python scripts/recalculate_profile_completion.py \
  --target production \
  --database-url 'mysql://<user>:<pass>@<host>:3306/<db>' \
  --allow-production \
  --dry-run

# 确认 dry-run 分布后，去掉 --dry-run 执行
```

生产必须显式提供 `--database-url` 与 `--allow-production`；脚本不会猜测生产库。

### 3.3 单用户排障

```bash
.venv/bin/python scripts/recalculate_profile_completion.py \
  --target development --user-id 123 --dry-run
```

## 4. 输出解读

```text
target=test dry_run=False users=1000
progress=500/1000 scanned=500 changed=312 failed=0
summary scanned=1000 changed=612 unchanged=388 grandfathered=57 failed=0 zero=12 middle=864 full=124
```

- `changed`：展示分或落库值发生变化的用户数。
- `unchanged`：重算后与存量一致。
- `grandfathered`：保留门禁豁免的 Q23 存量用户。
- `zero/middle/full`：重算后的分数分布。
- 第二次 dry-run 期望 `changed=0`。

## 5. 验证 SQL

```sql
-- 1) 镜像值一致（期望 0）
SELECT COUNT(*) FROM users u
JOIN user_profile_completion c ON c.user_id = u.id
WHERE ABS(u.data_complete_rate - c.score) > 0.001;

-- 2) 版本分布
SELECT algorithm_version, COUNT(*) FROM user_profile_completion GROUP BY algorithm_version;

-- 3) grandfather 与分数分布
SELECT completion_grandfathered, COUNT(*),
       SUM(score >= 100) AS full_score,
       SUM(score < 100) AS not_full
FROM user_profile_completion GROUP BY completion_grandfathered;

-- 4) 边界抽查
SELECT COUNT(*) FROM user_profile_completion WHERE score = 0;
SELECT COUNT(*) FROM user_profile_completion WHERE score = 100;
```

接口验证：

```bash
curl -sS "$BASE/api/v1/users/me/completion" -H "Authorization: Bearer $TOKEN"
curl -sS "$BASE/api/v1/users/me/overview" -H "Authorization: Bearer $TOKEN"
```

同一账号两处 `score` / `completion_score` 必须一致；任一中间态满足 `score + Σ未完成项 weight = 100`。

## 6. 回滚

- 本脚本只改数据，无 schema 变更。
- 回滚方式：恢复执行前备份的 `user_profile_completion` 与 `users.data_complete_rate`。
- 不要依赖 Q23 down 作为本批回滚：回填保留 grandfather 行的 `algorithm_version='legacy-backfill-20261109'`，但已重算的 score 不会由 Q23 down 恢复。
- 回滚后若再次上线，按第 2 节顺序重跑。

## 7. 故障处理

| 现象 | 处理 |
| --- | --- |
| `target test` 报缺少 `BUSINESS_TEST_DATABASE_URL` | 配置测试库 URL，不要用开发库代替 |
| 生产报 `--allow-production` / `--database-url` 缺失 | 补参数；脚本拒绝猜测生产库 |
| 单用户失败 | 脚本继续处理其他用户并以退出码 1 结束；用 `--user-id` 单独重跑并查日志 |
| 子查询超时/连接中断 | 降低 `--batch-size`，分多次 `--limit` 执行；脚本幂等 |
| 分数分布异常（大量 0） | 先 dry-run 对比，检查 `user_profile` 关联列是否缺失；不要直接写库 |
