# Xuanshi AI API

宣誓爱的 FastAPI 后端。当前是可运行的接口服务：社区等模块已接测试服，其它域按模块联调。本仓是独立 Git 仓库，与工作区 `xuanshiai-vue/` 前后端分离。

详细命令、数据库初始化和 AI 门禁见 [项目操作文档](docs/DEVELOPMENT.md)。接口契约见 [docs/api/](docs/api/)。未完成门禁见 [docs/待完成事项.md](docs/待完成事项.md)。

## 环境要求

- Python 3.11+
- MySQL 8+
- Redis 7+
- 推荐 `uv` 管理依赖（也可用 venv + `pip install -e ".[dev]"`）

## 本地启动

在 `xuanshiai-backend/` 下：

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# 把 DATABASE_URL 里的 YOUR_MYSQL_PASSWORD 换成本机 MySQL 密码
uv run uvicorn app.main:app --reload
```

兼容入口：`python main.py`（读 `HOST`/`PORT`/`DEBUG`）。

开发环境默认 `AUTO_INIT_DB=true`，启动时会幂等执行 `database_setup_marriage.py`。staging/production 必须 `AUTO_INIT_DB=false`。

启动后：

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/api/v1/health`
- `http://127.0.0.1:8000/docs`

前端联调：当前 `xuanshiai-vue/api/config.uts` 的 `API_BASE_URL` 是 `http://127.0.0.1:8000`，与本仓默认本地服务一致。测试服 `https://xhztest.xyz` 不是当前工作区默认值。H5 冒烟常见端口是 `http://localhost:8080`（不是 `:5173`）。CORS 在 `.env` 的 `CORS_ORIGINS_RAW`。

AI 画像 / 搜索 / 匹配度 / 语音默认关闭；未批准时返回 `503 AI_FEATURE_DISABLED`。生产禁止 mock provider。

## 配置说明

`.env.example` 是带注释的模板，`.env` 是本地配置，不要提交。部署时用平台环境变量注入，尤其替换 `SECRET_KEY`、数据库密码和 Redis。

## 测试

```powershell
uv run pytest
uv run pytest tests/test_health.py -v
uv run pytest tests/test_health.py::test_health_endpoint -v
uv run ruff check .
```

AI 一期真实依赖验收用独立 compose（不连本地开发库），命令见 `docs/DEVELOPMENT.md` 第 10.6 节。

## 本地演示数据

C 端联调需要一套完整会员数据。两个脚本按职责分工，都只在 `development/testing` 执行：

```powershell
# 1) 会员本体：14 位演示会员的完整资料、认证材料、会员权益、积分、关系链、聊天、通知
uv run python scripts/seed_community_members.py

# 2) 社区内容：话题、动态、活动、纸飞机、Banner
uv run python scripts/seed_community_demo.py
```

两个脚本都可重复执行：写入以手机号 / user_id / 自然键做 UPSERT，不删除既有数据。
素材（头像、相册、各类认证证明图）由脚本从前端 `static/` 复制并转成 webp，
落在 `storage/uploads/<user_id>/demo-*`，可安全重跑。

演示会员统一密码 `password123`，用 `POST /api/v1/auth/test-login` 登录：

| 账号 | 用途 |
| --- | --- |
| `19730552884` | 主测试账号（同时是红娘工作台账号），资料/会员/关系链最完整 |
| `13800001001` ~ `13800001016` | 14 位演示会员，覆盖南京/上海/杭州/北京，含 1 位离异 |

逐项验证 C 端功能（需后端已在 127.0.0.1:8000 运行）：

```powershell
uv run python scripts/verify_community_members.py
```

脚本用真实 HTTP 调用检查 74 项 C 端能力，输出通过数与失败明细。第三方依赖：
**Redis 必须可用**（每日额度接口 fail-closed），本地用 `D:/xuanshiai_runtime/redis-windows` 下的 `redis-server.exe` 启动。

### 已知的库结构漂移

`user_auth` 的学历/房产认证审核字段（`education_submitted_at`、`house_fail_reason` 等）曾因
建表脚本的 `ALTER` 失败被静默跳过，导致 `/users/me/certifications` 报 1054。启动前先体检：

```powershell
uv run python scripts/report_db_drift.py          # 只读，退出码 1 表示存在缺口
uv run python database_setup_marriage.py          # 幂等补齐，无 DROP/DELETE
```

## 目录说明

```text
app/
  api/       HTTP 路由；聚合点 app/api/router.py，前缀 /api/v1
  core/      配置、鉴权、日志
  db/        数据库连接与会话
  models/    ORM 模型
  schemas/   Pydantic 请求/响应
  services/  业务服务
  workers/   AI Worker 等后台进程
docs/        操作文档；接口契约在 docs/api/
tests/       pytest
database_setup_marriage.py  开发/测试幂等建表
compose.ai-test.yml         AI 集成测试专用 MySQL/Redis/Worker
storage/     本地上传与运行时文件
logs/        本地日志
```

## AI 编码规则

改代码前先读：

- `AGENTS.md`：Codex 规则入口
- `CLAUDE.md`：Claude Code 规则入口
- `PROJECT_RULES.md`：共用规则正文（接口契约、安全、敏感信息、改动总结）
