# 业务 schema baseline 与契约校验

## 目的

新环境不再只能靠 `database_setup_marriage.py` 的隐藏补列（`_ensure_required_columns`、
`_ensure_filter_columns`、`_ensure_optional_index`）来"猜"完整结构：

1. `migrations/baseline/schema.sql` 记录一个**已验证版本**的完整结构；
2. `migrations/business/manifest.json` 按版本顺序执行增量迁移；
3. `scripts/schema_contract.py` 把结果与代码声明的结构逐列比对，不一致就让 CI 失败。

这条链路专门覆盖 2026-10-27 评估里的 P0-1：`ALTER TABLE ... ADD COLUMN` 失败曾被降级成
WARNING（提交 `8ec8ff1` 的事故），生产 `AUTO_INIT_DB=false` 时缺列只能等 500 才暴露。

## 生成 baseline（人工、只读源库）

baseline 必须来自**已验证**的库（测试/预发布，且刚跑过 bootstrap 或已执行完全部迁移），
不能手写：

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe scripts\dump_baseline_schema.py `
  --database-url "mysql://root:实际密码@127.0.0.1:3306/xuanshiai_verify" `
  --confirm-database xuanshiai_verify
```

脚本会：

- 只读（`information_schema` + `SHOW CREATE TABLE`），不执行任何 DDL/DML；
- 拒绝库名含 `prod` / `production` / `live` 的目标；
- 去掉 `AUTO_INCREMENT=<n>` 这类每库不同的噪声；
- 生成 `migrations/baseline/schema.sql` 与 `SHA256SUMS`。

生成后立刻验证：

```powershell
.\.venv\Scripts\python.exe scripts\schema_contract.py check-db `
  --database-url "mysql://root:实际密码@127.0.0.1:3306/xuanshiai_verify"
```

## 刷新规则

- baseline 只在**发布窗口**刷新，且必须与当次迁移版本一起提交；
- 刷新前先确认目标库 `business_schema_migration` 的 `version/status` 全部为 `succeeded`；
- 不允许在 baseline 里保留业务数据（只有 DDL）；
- 刷新后 CI 的 "Baseline + migrations" job 必须绿，否则不得发布。

## CI 校验（`.github/workflows/schema-contract.yml`）

| 步骤 | 校验内容 |
|---|---|
| Bootstrap | 用代码建一遍库（`AUTO_INIT_DB=true`），暴露 bootstrap 自身的失败 |
| Contract | `schema_contract.py check-db`：表/列/索引/主键缺失或类型不一致即失败 |
| Unit | `tests/test_schema_contract.py`、`tests/test_business_schema_migration_contract.py` |
| Baseline | baseline 存在时：导入空库 + 执行 business 迁移，再跑一次 contract |

> baseline 尚未提交前，最后一步自动跳过（工作流里用 `hashFiles` 判断）。