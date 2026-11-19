# paper_plane / chat 域迁移 Runbook（2026-11-09）

> 任务标识：`UT-2026-11-02-DOC-02-BE-20261109`

## 1. 适用范围

`scripts/manage_domain_migration.py` 只处理：

| 域 | manifest | 台账 |
| --- | --- | --- |
| `paper_plane` | `migrations/paper_plane/manifest.json` | `domain_schema_migration`（`domain` 列区分） |
| `chat` | `migrations/chat/manifest.json` | 同上 |

business 用 `scripts/manage_business_migration.py`；AI 用 `scripts/manage_ai_migration.py`，不要混用。

## 2. 命令

```bash
# 状态
python scripts/manage_domain_migration.py status --domain chat --target test

# 增量执行某版本（推荐）
python scripts/manage_domain_migration.py up --domain paper_plane --target test \
  --version 20261109_01_unlock_cost_default

# 校验
python scripts/manage_domain_migration.py verify --domain paper_plane --target test \
  --version 20261109_01_unlock_cost_default

# 回滚（确认备份与数据影响后）
python scripts/manage_domain_migration.py down --domain paper_plane --target test \
  --version 20261109_01_unlock_cost_default
```

`--target development` 读 `DATABASE_URL`；`--target test` 读 `BUSINESS_TEST_DATABASE_URL`。生产用显式 URL：

```bash
python scripts/manage_domain_migration.py up --domain chat \
  --database-url 'mysql://user:pass@host:3306/db' --allow-production
```

## 3. verify 语义

- `verify_mode=empty`：verify 必须是单条查询，返回 0 行才算通过（paper_plane 的 Q45 文件）。
- `verify_mode=info`：verify 可以多条信息性查询，runner 只执行并打印行数，不因有行失败（chat 现有 verify）。
- 新增迁移优先用 `empty`；若要沿用信息性 verify，必须在 manifest 显式写 `"verify_mode": "info"`。

## 4. 执行约束

1. runner 拒绝 `DELIMITER`；down 必须是逐条可执行 SQL。Q45 的 down 已按此规则改写。
2. 每条 up/down 都记录在 `domain_schema_migration`；重复执行会更新状态。
3. `paper_plane` 的 `20260903_01/02` 没有 verify，runner 只执行 up/down 并记录；新增版本应补 verify。
4. `chat` 的 down 会丢隐藏状态/幂等键数据，执行前必须确认后端版本与备份。
5. 生产保持 `AUTO_INIT_DB=false`，不要在应用启动时自动跑迁移。

## 5. 已验证场景（scratch 库）

- paper_plane `20261109_01_unlock_cost_default`：up → verify → status → down，全部 EXIT 0。
- chat `20261106_01_chat_message_hidden`：up → verify（info，8 行）→ down，全部 EXIT 0。
- 台账 `domain_schema_migration` 正确记录 `succeeded/rolled_back`。
