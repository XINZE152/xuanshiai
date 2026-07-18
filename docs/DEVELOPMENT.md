# 项目操作文档

本文档记录 Xuanshi AI API 后端项目的常用环境、启动、测试、检查和维护命令。默认命令使用 Windows PowerShell，并在项目根目录 `E:\houduan\xuanshiai` 执行。

## 一、环境要求

- Python 3.11 或更高版本
- MySQL 8 或更高版本
- Redis 7 或更高版本
- Git
- 推荐安装 `uv`，用于创建虚拟环境和管理依赖

检查工具是否可用：

```powershell
python --version
git --version
uv --version
```

## 二、首次初始化

### 方式 A：使用 uv（推荐）

```powershell
uv sync --extra dev
```

该命令会创建 `.venv`、安装运行依赖和开发依赖，并根据 `pyproject.toml` 更新 `uv.lock`。

### 方式 B：使用 Python venv 和 pip

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

如果 PowerShell 阻止激活脚本，可以直接使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\pytest.exe`。

## 三、环境变量配置

首次使用时复制模板：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`：

```powershell
code .env
```

重点配置项：

| 配置项 | 用途 | 本地默认值 |
| --- | --- | --- |
| `DATABASE_URL` | MySQL 异步连接地址 | `mysql+aiomysql://root:YOUR_MYSQL_PASSWORD@127.0.0.1:3306/xuanshiai` |
| `REDIS_URL` | Redis 连接地址 | `redis://127.0.0.1:6379/0` |
| `SECRET_KEY` | JWT 签名密钥 | 仅开发占位值，部署前必须替换 |
| `CORS_ORIGINS_RAW` | 允许跨域的前端地址 | `http://localhost:3000,http://localhost:5173` |
| `UPLOAD_DIR` | 上传文件目录 | `storage/uploads` |

`.env` 包含本地密钥和连接信息，不要提交到 Git。完整配置说明见 `.env.example`。

### MySQL 项目数据库配置

1. 打开 `.env`，把 `YOUR_MYSQL_PASSWORD` 替换为你安装 MySQL 时设置的 `root` 密码：

```env
DATABASE_URL=mysql+aiomysql://root:你的密码@127.0.0.1:3306/xuanshiai
```

2. 使用 MySQL 客户端登录并创建项目数据库。下面命令会提示输入密码，不会把密码写进命令历史：

```powershell
& 'H:\mysql\bin\mysql.exe' --protocol=TCP --host=127.0.0.1 --port=3306 --user=root --password
```

登录后执行：

```sql
CREATE DATABASE IF NOT EXISTS xuanshiai
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

退出 MySQL：

```sql
EXIT;
```

3. 如果密码中包含 `@`、`:`、`/`、`#` 或空格，需要先进行 URL 编码，再填入 `DATABASE_URL`。例如 `@` 编码为 `%40`。

4. 验证数据库连接。命令会提示输入密码，并执行 `SELECT 1`：

```powershell
Test-NetConnection 127.0.0.1 -Port 3306
& 'H:\mysql\bin\mysql.exe' --protocol=TCP --host=127.0.0.1 --port=3306 --user=root --password --database=xuanshiai --execute="SELECT 1 AS connection_ok;"
```

当前项目已预留数据库连接配置，但尚未自动执行建表迁移；后续接入 ORM 模型后，再增加 Alembic 迁移命令。

## 四、启动服务

### 开发模式

```powershell
uv run uvicorn app.main:app --reload
```

兼容入口：

```powershell
python main.py
```

默认访问地址：

```text
根路径：   http://127.0.0.1:8000/
健康检查： http://127.0.0.1:8000/api/v1/health
Swagger：   http://127.0.0.1:8000/docs
ReDoc：     http://127.0.0.1:8000/redoc
```

指定其他端口：

```powershell
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

生产模式示例：

```powershell
$env:ENVIRONMENT = "production"
$env:DEBUG = "false"
$env:DOCS_ENABLED = "false"
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 五、测试与代码检查

运行全部测试：

```powershell
uv run pytest
```

运行单个测试文件或指定测试：

```powershell
uv run pytest tests/test_health.py -v
uv run pytest tests/test_health.py::test_health_endpoint -v
```

运行 Ruff 代码检查：

```powershell
uv run ruff check .
```

自动修复 Ruff 可以修复的问题：

```powershell
uv run ruff check . --fix
```

检查 Python 语法和编译：

```powershell
uv run python -m compileall -q app main.py
```

每次修改代码后至少执行：

```powershell
uv run ruff check .
uv run pytest
```

## 六、数据库和 Redis 检查

```powershell
Test-NetConnection 127.0.0.1 -Port 3306
Test-NetConnection 127.0.0.1 -Port 6379
```

项目已经预留 `app/db`、`app/models`、`app/schemas` 和 `app/services` 目录。新增业务模块时，先确认数据库模型、迁移方案和接口契约，再接入实际数据库连接。

## 七、Git 常用命令

```powershell
git status
git diff
git status --short
```

提交前建议依次执行：

```powershell
uv run ruff check .
uv run pytest
git diff --check
git status
```

不要提交 `.env`、真实密钥、数据库密码、`.venv`、`.uv-cache`、缓存、上传文件和运行日志。

## 八、项目目录

```text
app/
  api/       HTTP 路由和 API 聚合
  core/      配置和基础设施
  db/        数据库连接与会话
  models/    ORM 模型
  schemas/   Pydantic 请求/响应模型
  services/  业务服务层
docs/        项目操作和开发文档
tests/       自动化测试
storage/     本地运行时文件
logs/        本地日志目录
```

## 九、AI 编码工具规则

使用 Codex 或 Claude Code 修改代码前，必须先阅读项目根目录的 `AGENTS.md` 或 `CLAUDE.md`，并遵守其中引用的 `PROJECT_RULES.md`。

规则正文预留在 `PROJECT_RULES.md`，由项目负责人持续补充。
