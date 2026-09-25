# chat_message 幂等键迁移

`20260923_01_chat_message_idempotency_up.sql` 为 `chat_message` 增加可空 `client_message_id varchar(128)`，并建立 `(from_user_id, session_id, client_message_id)` 唯一键。键列使用 `utf8mb4_bin`，按大小写敏感的字节值区分不同 ID。`verify.sql` 检查列类型、可空性、排序规则、唯一索引列顺序及重复非空键；`down.sql` 幂等删除该索引和列。

up/down 通过 `INFORMATION_SCHEMA` 检查后执行动态 DDL，可在部分执行后安全重跑。迁移不回填或修改历史消息，历史行的 `client_message_id` 保持 `NULL`，MySQL 复合唯一索引允许这些历史行共存。

本仓库对聊天表没有通用迁移 runner；请按现有 live 模块人工 SQL 迁移方式执行，AI 专用 `scripts/manage_ai_migration.py` 不适用于此迁移。先确认已选定正确的目标数据库并完成备份，不要对生产环境自动运行 `database_setup_marriage.py`：

1. 执行 `20260923_01_chat_message_idempotency_up.sql`。
2. 执行 `20260923_01_chat_message_idempotency_verify.sql`。确认列为 `varchar(128)`、`IS_NULLABLE=YES`、`COLLATION_NAME=utf8mb4_bin`；索引列顺序为 `from_user_id,session_id,client_message_id` 且 `NON_UNIQUE=0`；重复键计数为 `0`。
3. 再发布使用新字段的后端。开发/测试环境在 `AUTO_INIT_DB=true` 时，`database_setup_marriage.py` 也会幂等补齐同一列和唯一键；生产/预发布继续保持 `AUTO_INIT_DB=false`，只使用显式迁移。

回滚前停止依赖该字段的后端写入并确认与代码版本同步，再执行 `20260923_01_chat_message_idempotency_down.sql`。down 保留 `chat_message` 和消息正文，但会删除已经写入的 `client_message_id` 值，因此只能在放弃幂等键数据后使用；down 可重复执行。