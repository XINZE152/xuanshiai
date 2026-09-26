# 后端全量测试与 Ruff 历史治理清单

> 记录日期：2026-09-25。本文记录当前后端全量无集成检查的真实结果，用于区分消息/媒体稳定门禁与存量治理项。不删除、跳过或改写失败用例，不把这些结果描述为本轮消息链路已通过。

## 当前门禁结果

| 检查 | 命令 | 结果 | 结论 |
| --- | --- | --- | --- |
| 消息/申请/媒体定向测试 | `python -m pytest tests/test_social_features.py tests/test_social_message_idempotency.py tests/test_discovery_features.py tests/test_member_follow_up_media.py -q` | `60 passed` | 本轮消息/媒体定向门禁通过 |
| 后端全量无集成测试 | `python -m pytest tests --ignore=tests/integration --ignore=tests/live --ignore=tests/manual -q` | `2129 passed, 6 failed, 6 skipped, 22 warnings` | 全量仍未收绿 |
| 全仓 Ruff | `python -m ruff check .` | `56 errors` | 全仓静态检查仍未收绿 |
| 前端核心门禁 | `cd ../xuanshiai-vue && npm test` | `13/13 passed` | 前端消息核心门禁通过 |

后端全量检查未连接共享开发或生产 MySQL/Redis。6 个跳过项来自显式的隔离数据库或 Windows 运行能力条件，不能作为通过证据。

## Pytest 失败清单

### 1. M4/M7 迁移资产缺失

- **测试**：`tests/test_m4_m7_column_migration.py::test_m4_m7_migration_scripts_are_paired`
- **失败信号**：`migrations/m4_m7/20260916_01_m4_m7_backoffice_columns_up.sql` 与对应 `down.sql` 不存在。
- **责任模块**：`migrations/m4_m7/` 迁移资产；同时需要核对 `tests/test_m4_m7_column_migration.py` 与当前迁移目录命名约定。
- **当前归属判断**：本轮后端工作树未修改 `migrations/` 或该测试；属于存量迁移资产/基线问题，不能归因于消息或媒体改动。
- **修复要求**：确认目标迁移是否应恢复为受控的 up/down 成对文件，或更新测试以匹配已批准的迁移目录；必须先核对数据库发布与回滚策略，不能只创建空文件满足断言。
- **回归命令**：`python -m pytest tests/test_m4_m7_column_migration.py -q`
- **处置结果（2026-09-27）**：已补齐受控迁移资产 `migrations/m4_m7/20260916_01_m4_m7_backoffice_columns_{up,down,verify}.sql` 与 `README.md`，逐条镜像 `database_setup_marriage.py` 的 `_ensure_m4..m7` helper（`database_setup_marriage.py:3384-3502`）。发布与回滚方式见 `migrations/m4_m7/README.md`；`down` 恢复 `commission_entry.order_id NOT NULL` 仅当表内无 `order_id IS NULL` 数据时执行，否则脚本跳过该步并输出 `rollback_blocked` 提示。已在 `compose.ai-test.yml` 的独立测试 MySQL（127.0.0.1:3307）一次性临时库上完成 up×2（幂等）/verify/down×2（幂等）/再 up/NULL 数据回滚阻塞的全路径演练后删除临时库。

### 2. 生产 Mock 支付错误契约不一致

- **测试**：`tests/test_pricing_config.py::test_mock_wechat_payment_is_rejected_outside_test_environments`
- **失败信号**：`Settings(... environment="production", wechat_payment_mode="mock")` 确实被拒绝，但 `ValidationError` 展示文本未匹配测试期待的 `微信支付 Mock`。
- **责任模块**：`app/core/config.py` 的生产配置校验与 `tests/test_pricing_config.py` 的错误契约。
- **当前归属判断**：本轮后端工作树未修改 `app/core/config.py` 或该测试；属于既有错误信息/测试契约漂移。安全行为仍然是 fail-closed，不能为了让测试通过而放宽生产 Mock 禁止规则。
- **修复要求**：统一稳定错误码或稳定错误消息，并同步测试；优先使用可供客户端/运维识别的错误码，避免测试依赖 Pydantic 拼装后的完整字符串。
- **回归命令**：`python -m pytest tests/test_pricing_config.py::test_mock_wechat_payment_is_rejected_outside_test_environments -q`

### 3. 生产真实支付模式被其他生产门禁拦截

- **测试**：`tests/test_pricing_config.py::test_real_wechat_payment_mode_is_available_for_production`
- **失败信号**：`wechat_payment_mode="real"` 的 `Settings` 构造在支付字段断言前失败，当前异常来自生产配置/AI 门禁组合，而不是 `wechat_payment_mode` 字段本身。
- **责任模块**：`app/core/config.py` 的生产配置默认值与 `_validate_ai_feature_gates()`，以及 `tests/test_pricing_config.py` 的最小生产配置夹具。
- **当前归属判断**：本轮后端工作树未修改配置实现或该测试；属于生产设置默认值与测试隔离不完整的存量问题。修复时不得削弱 AI 生产 fail-closed。
- **修复要求**：让“验证真实支付模式可用”的测试显式提供所有无关生产门禁所需的关闭值，或拆分纯支付配置校验，保证测试只验证目标契约。
- **回归命令**：`python -m pytest tests/test_pricing_config.py::test_real_wechat_payment_mode_is_available_for_production -q`

### 4. 资料标签目录与测试数据不一致

- **测试**：`tests/test_profile_features.py::test_profile_validates_mbti_height_and_tags`
- **失败信号**：`interest_tags=["健身", "旅行", "摄影"]` 未通过当前 `ALL_TAG_OPTIONS` 校验。
- **责任模块**：`app/core/profile_tags.py` 标签目录、`app/schemas/auth.py` 兼容字段校验、对应标签同步测试/文档。
- **当前归属判断**：本轮后端工作树未修改标签目录、资料 Schema 或该测试；属于目录版本与测试快照漂移。前端标签同步检查另有错误的后端根目录参数，不能把该检查当作目录已一致的证据。
- **修复要求**：确定当前产品标签目录的唯一来源，更新测试数据或目录并同步前端契约；保留自定义标签和旧字段兼容规则的安全校验。
- **回归命令**：`python -m pytest tests/test_profile_features.py -q`；跨仓再执行 `cd ../xuanshiai-vue && node tests/test-personal-tags.js`

### 5. 行政区编码未归一化

- **测试**：`tests/test_regions.py::test_region_route_codes_can_be_normalized_to_tree_codes`
- **失败信号**：`list_cities("110000")` 未归一化到树编码 `"11"`，直接返回 404；同类问题存在于 `list_districts("110100")`。
- **责任模块**：`app/services/regions.py` 的省/市编码归一化，以及 `tests/test_regions.py`。
- **当前归属判断**：本轮后端工作树未修改地区服务或该测试；属于存量服务契约缺口，与消息/媒体无关。
- **修复要求**：在服务边界统一处理 2/4/6 位行政区编码，保留非法编码 404；补充省、市、区三级及空值/异常长度回归覆盖。
- **回归命令**：`python -m pytest tests/test_regions.py -q`

### 6. 语音 WebSocket 生产 fail-closed 测试夹具被配置门禁拦截

- **测试**：`tests/test_voice_ws.py::test_production_fail_closed`
- **失败信号**：测试想验证 WebSocket 关闭行为，但 `Settings(...)` 在建立客户端前因生产配置门禁抛出 `ValidationError`。
- **责任模块**：`app/core/config.py` 生产 AI 门禁、`tests/test_voice_ws.py` 的生产测试设置夹具。
- **当前归属判断**：本轮后端工作树未修改配置实现或该测试；语音路由本身仍保留关闭时返回 WebSocket policy violation 的 fail-closed 路径，当前失败首先是夹具无法构造设置。
- **修复要求**：给测试提供满足“AI 全部关闭”的最小生产配置，或将路由门禁测试与 Settings 构造测试分离；不能关闭生产门禁来迁就测试。
- **回归命令**：`python -m pytest tests/test_voice_ws.py::test_production_fail_closed -q`

## Ruff 诊断清单

本次 `python -m ruff check . --output-format=json` 共 56 项：`F401=36`、`F841=10`、`E702=6`、`E402=2`、`F821=2`。以下按文件完整列出，作为后续责任模块和回归范围索引。

### 业务代码

| 文件 | 行号 | 规则 | 责任/建议 |
| --- | ---: | --- | --- |
| `app/api/routes/activity_admin.py` | 13, 25 | `F401` ×3 | 清理未使用的 `CurrentUser`、`get_current_admin`、`ActivitySignupStatusUpdate`；回归活动后台路由测试 |
| `app/api/routes/ai_compatibility.py` | 35 | `F401` | 清理 `CompatibilitySnapshotRead`；回归兼容性路由测试 |
| `app/api/routes/finance.py` | 278 | `F821` | 补齐或修正 `admin_grant_credits` 的导入/调用契约；回归财务管理测试 |
| `app/api/routes/matchmaker.py` | 26, 47 | `F401` ×5 | 清理未使用的评分 Schema 与服务导入；确认评分路由是否遗漏后再回归红娘路由测试 |
| `app/api/routes/member_media_admin.py` | 20 | `F401` | 清理 `MemberMediaItem`；回归后台媒体列表/替换测试 |
| `app/api/routes/merchant_admin.py` | 307 | `F821` | 补齐 SQLAlchemy `text` 导入；回归商家订单详情路由测试 |
| `app/services/ai/audit.py` | 220 | `F401` | 清理或实际使用 `pymysql` 导入，核对审计写入分支后回归 AI 审计测试 |
| `app/services/matchmaker_workspace.py` | 82, 85 | `F401` ×2 | 清理未使用的工作台状态 Schema；回归工作台服务测试 |
| `app/services/member_auth_admin.py` | 768, 770, 772, 774, 776, 778 | `E702` ×6 | 将分号连接的赋值拆为独立语句；回归会员后台资料类型更新测试 |
| `app/services/organization_admin.py` | 243 | `F841` | 删除未使用的 `user_scope` 或补回实际 SQL 使用；回归组织报表测试 |
| `app/services/voice/stream_tts_provider.py` | 35, 120 | `F401` ×2 | 清理未使用的阿里云异常与 Token TTL 导入；回归流式 TTS 客户端测试 |

### AI 集成测试代码

| 文件 | 行号 | 规则 | 责任/建议 |
| --- | ---: | --- | --- |
| `tests/integration/ai/test_ai_compatibility_engine_real_db.py` | 18 | `F401` | 清理未使用的 `CompatibilitySnapshotStatus`；在隔离 MySQL/Redis 集成环境回归 |
| `tests/integration/ai/test_ai_entry_profile_real_db.py` | 528 | `F841` | 删除未使用的 `projection_b` 或补充断言；在隔离 MySQL 集成环境回归 |
| `tests/integration/ai/test_ai_memory_phase3_real_db.py` | 26, 154 | `F401`, `F841` | 清理 `MemoryLedger` 导入并处理 `claim_id`；在隔离 MySQL 集成环境回归 |
| `tests/integration/ai/test_ai_memory_projection_real_db.py` | 13, 22, 24 | `F401` ×3 | 清理未使用类型、Ledger 和异常导入；在隔离 MySQL 集成环境回归 |
| `tests/integration/ai/test_ai_memory_real_db.py` | 657, 665 | `F841` ×2 | 删除未使用的 `session`/`records` 或补充断言；在隔离 MySQL 集成环境回归 |

### 普通测试代码

| 文件 | 行号 | 规则 | 责任/建议 |
| --- | ---: | --- | --- |
| `tests/test_ai_compatibility_compare.py` | 12, 14 | `F401` ×2 | 清理未使用的比较类型导入；回归该测试文件 |
| `tests/test_ai_memory_derivations.py` | 40, 42 | `E402` ×2 | 调整模块导入顺序；回归该测试文件及内存派生测试 |
| `tests/test_ai_memory_materializer.py` | 21, 22, 202 | `F401` ×2, `F841` | 清理未使用 Fake Session/Store 并处理 `corrected`；回归物化器测试 |
| `tests/test_ai_memory_projection_outbox.py` | 21 | `F401` | 清理 `GRANT_KWARGS`；回归 outbox 测试 |
| `tests/test_ai_memory_projections.py` | 20, 459, 554 | `F401` ×2, `F841` | 清理未使用策略/函数导入并处理 `key`；回归投影测试 |
| `tests/test_apportion_config_admin.py` | 17, 25 | `F401` ×2 | 清理未使用路由模块和 Schema 导入；回归分摊配置后台测试 |
| `tests/test_backfill_ai_memory.py` | 16 | `F401` | 清理 `asyncio`；回归 backfill 测试 |
| `tests/test_bulk_materialization.py` | 222 | `F841` | 删除或使用 `baseline`；回归批量物化测试 |
| `tests/test_m4_finalize_ext.py` | 13 | `F401` ×2 | 清理未使用 `AsyncMock`/`MagicMock`；回归 M4 扩展测试 |
| `tests/test_m7_activity_merchant_video_ext.py` | 150 | `F401` | 清理未使用 `timedelta`；回归 M7 扩展测试 |
| `tests/test_m9_system_finance_ext.py` | 86 | `F401` | 清理未使用 `app.api.router`；回归系统财务测试 |
| `tests/test_persona_cache_invalidation.py` | 18 | `F401` | 清理未使用 `PersonaProjectionSession`；回归 Persona 缓存测试 |
| `tests/test_stream_tts_client.py` | 12 | `F401` | 清理未使用 `MagicMock`；回归流式 TTS 测试 |
| `tests/test_task_websocket_notifications.py` | 497, 519 | `F841` ×2 | 删除或使用未使用的 `db`；回归任务 WebSocket 通知测试 |

## 处理边界与优先级

1. **P0/P1 安全与生产门禁**：先修复配置测试夹具/错误契约，但保持生产 Mock、AI 和语音 fail-closed；不得为了全量收绿而放宽门禁。
2. **P1 迁移与公开契约**：先确认 M4/M7 迁移资产来源、发布窗口、备份和回滚，再恢复或调整迁移测试。
3. **P1 业务契约**：补齐地区编码归一化和资料标签目录同步，分别补服务层回归与跨仓目录检查。
4. **P2 静态清理**：按业务模块批量处理 Ruff，业务代码与 AI 集成测试分开回归；`F821` 必须优先于纯未使用导入清理。
5. 全量检查稳定后，才评估是否把后端 CI 从消息/媒体改动域扩大到全仓。

## 当前工作树归属

- 本轮后端变更路径：`.github/workflows/backend-ci.yml`、`docs/DEVELOPMENT.md`、`.gitignore`、`README.md`、`docs/MEDIA_STORAGE_ACCESS_REVIEW.md`、`scripts/reproduce_upload_exposure.py`。
- 本轮未修改上述 6 个失败对应的实现/测试，也未修改 Ruff 报告中的业务文件。
- 因此 6 个 pytest 失败和 56 个 Ruff 错误均登记为存量治理项，不阻塞已通过的消息/媒体定向门禁，但阻塞后端全仓绿色。

## 不可替代的验证

- 本清单不替代真实 HTTP 消息联调、隔离 MySQL/Redis 集成测试、HBuilderX 构建、微信开发者工具、真机和 HTTPS/部署验收。
- `scripts/reproduce_upload_exposure.py` 的匿名 `200` 结果仍是 P0 媒体风险证据，不是通过标准。
- 当前未执行提交、推送、发布或上线。
