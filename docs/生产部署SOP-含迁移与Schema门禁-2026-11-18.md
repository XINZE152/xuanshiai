# 生产部署 SOP（含迁移与 Schema 门禁）

- 文档版本：1.0.0
- 日期：2026-11-18
- 任务标识：`DEPLOY-SOP-20261118`
- 触发事件：2026-11-18 生产事故 —— 部署后 `PATCH /users/me/profile` 500（29 次）、`/ai/aibot/assignment` 500（9 次），小程序端表现为「填写资料下一步按不了」「网络异常，没能连上红娘服务」，并伴随一次 502。
- 适用范围：`api.xuanshiai.com`（腾讯云 `VM-0-13-ubuntu` / `118.195.138.31`，项目路径 `/home/xuanshiai`）。

---

## 一、事故复盘

### 时间线（2026-11-18）

| 时间 | 事件 |
| --- | --- |
| 15:09:19 | 老进程 `pid 2899946` 正常服务（后台管理接口全 200） |
| 15:11:21 | `systemctl restart xuanshiai` → 新进程 `pid 3198059` 启动（`NRestarts=0`，即计划内重启，非崩溃） |
| 15:11:25 | nginx 记录 `connect() failed (111)` → 返回 **502**（上游 8000 端口尚未监听） |
| 15:17:13 | 应用日志 `OperationalError (1054, "Unknown column 'p.bot_intake_json' in 'field list'")` |
| 15:26:09 | 补跑缺失迁移完成 |
| 15:26 之后 | 5xx 归零，`/users/me/profile` 连续 14 次 200 |

### 根因链

```
11-17 提交恋 AI BOT 功能
  ├─ 新增 4 张表 + user_profile.bot_intake_json 列
  ├─ 迁移 SQL 放进 migrations/aibot/ 目录
  │    └─ 该目录 manifest.json 为 0 字节空文件，且没有任何 runner 接管
  │       （manage_ai_migration.py 只认 migrations/ai/，manage_business_migration.py 只认 migrations/business/）
  │       ⇒ 迁移永远不会被执行  ← 根因
  ├─ bootstrap（database_setup_marriage.py / ai_schema.py）也未包含这些表
  │    ⇒ 全新部署的库同样缺失
  └─ 部署流程 = 手工 git pull + systemctl restart
       └─ 无迁移步骤、无 schema 门禁  ← 根因放大器

11-18 15:11 部署上线
  └─ 代码查 p.bot_intake_json，库无此列 ⇒ 资料接口与红娘绑定接口全量 500
```

### 为什么 `migrations/aibot/` 里的迁移从未执行

| 事实 | 证据 |
| --- | --- |
| `migrations/aibot/manifest.json` 是 0 字节空文件 | 本地与服务器均如此 |
| 无 runner 接管该目录 | `manage_ai_migration.py:27` `MIGRATION_ROOT = ROOT/"migrations"/"ai"`；`manage_business_migration.py:24` 同理指向 `business` |
| 两个 runner **都不支持生产目标** | `--target choices=("test","development")`；`_database_config_for_target` 无 production 分支 |
| 服务器无任何部署脚本 | `/home/xuanshiai/*.sh` 为空；`.deploy_*.sh` 只存在于旧服务器且从未入库 |
| bootstrap 缺表 | `database_setup_marriage.py` 与 `app/db/ai_schema.py` 均无 `aibot_*` 建表语句 |

---

## 二、标准部署流程（六步，缺一不可）

> 顺序原则：**备份 → 拉代码 → 迁移 → 门禁 → 重启 → 验证**。迁移必须先于重启，否则新代码会撞上旧结构。

### 第 1 步：备份（不可跳过）

```bash
cd /home/xuanshiai
TS=$(date +%Y%m%d_%H%M%S)
DBU=$(grep -oP 'mysql\+aiomysql://\K[^:]+' .env)
DBP=$(grep -oP 'mysql\+aiomysql://[^:]+:\K[^@]+' .env)
DBN=$(grep -oP 'mysql\+aiomysql://[^@]+/\K.+' .env)
export MYSQL_PWD="$DBP"
mysqldump -u"$DBU" --single-transaction --skip-lock-tables --routines --triggers "$DBN" \
  > "/home/ubuntu/backup_xuanshiai_${TS}.sql"
ls -la "/home/ubuntu/backup_xuanshiai_${TS}.sql"   # 必须 > 0 字节
```

> **注意**：不要加 `--events`（会因账号缺 EVENT 权限直接失败）；`MYSQL_PWD` 传入前务必确认 `mysqldump` 退出码为 0 且文件非空。
> 密码若含 `#` 等字符，`urlparse` 会解析失败（当成 URL fragment），提取时改用 `rpartition("@")` 手动切分。

### 第 2 步：拉代码

```bash
cd /home/xuanshiai && git pull --ff-only origin main && git log --oneline -1
```

### 第 3 步：应用迁移

当前生产**没有合法的迁移执行入口**（runner 不支持 production），迁移按 reviewed SQL 手工执行，**执行前必须核对列/表是否已存在**：

```bash
cd /home/xuanshiai
# 逐个版本检查对象是否已存在，再决定执行哪个 up
# 例：查 user_profile.bot_intake_json
mysql -u"$DBU" -p"$DBP" "$DBN" -e "SHOW COLUMNS FROM user_profile LIKE 'bot_intake_json';"
```

> 若本次上线包含 `ALTER TABLE ... ADD COLUMN`，**重复执行会报 1060 Duplicate column**。先查后执行。
> `CREATE TABLE IF NOT EXISTS` 型迁移可安全重复执行。

### 第 4 步：发布前 Schema 门禁（本次新增，必跑）

```bash
cd /home/xuanshiai
sudo .venv/bin/python scripts/check_release_schema.py \
  --database-url "mysql://$DBU:$DBP@127.0.0.1:3306/$DBN" --allow-production
```

退出码：`0` 全部通过 / `1` 有缺失 / `2` 配置或连接错误。**退出码非 0 不得继续重启服务。**

> 门禁清单以 `scripts/check_release_schema.py` 的 `collect()` 为准。问题 7 另查 `user_privacy`、`user_revision_state`、`derivation_outbox` 的设置保存依赖列。能读取设置不代表能写入；若任一检查失败，先核对目标库已有迁移和缺失对象，不要跳过外发事件写入或直接重跑全量初始化。

### 第 5 步：重启

```bash
sudo systemctl restart xuanshiai
sudo systemctl status xuanshiai --no-pager
```

### 第 6 步：验证

```bash
# 6.1 接口自检
curl -s -o /dev/null -w "page-switches=%{http_code}\n" https://api.xuanshiai.com/api/v1/public/page-switches
# 6.2 日志无 5xx
sudo journalctl -u xuanshiai --since "-5min" --no-pager | grep -E 'status=5[0-9][0-9]'
# 6.3 结构门禁复查
sudo .venv/bin/python scripts/check_release_schema.py --database-url "..." --allow-production
```

---

## 三、502 的成因与消除方案

### 成因

nginx 单上游、无冗余：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;   # 唯一上游
    proxy_read_timeout 300s;
}
```

systemd 单元：

```ini
ExecStart=.../python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ...
Restart=always
RestartSec=5
```

- **单 worker**：没有第二个进程可承接重启期间的流量
- **无 `--workers`**：并发全靠单进程事件循环
- 重启期间 uvicorn 需数秒完成启动（加载 AI provider、连 Redis/MySQL、初始化），此窗口内 nginx `connect() failed (111)` → 502
- nginx 未配 `upstream` + 健康检查，**任何**重启都有窗口

### 方案对比

| 方案 | 效果 | 改动面 | 风险 | 建议 |
| --- | --- | --- | --- | --- |
| A. 双实例 + nginx upstream | 重启期间零 502 | 新增 systemd 实例（:8001）+ nginx upstream + 健康检查 | 中（涉及生产 nginx/systemd） | **推荐**，但需专门窗口实施 |
| B. 灰度/维护模式 | 把 502 变成 503 + 友好提示 | 部署时先摘流量 | 低 | 可作为 A 之前的过渡 |
| C. 缩短重启窗口 | 减少但不消除 502 | 预热（先起后切）、`SO_REUSEPORT` | 低 | 收益有限 |

> **本 SOP 不擅自变更生产 nginx/systemd**：属「涉及生产/公开契约」的改动，需单独批准与回滚预案。

### 无 502 的关键补充

方案 A 之外，**把第 3、4 步做扎实（迁移 + 门禁）就已经消除了本次事故的全部用户可见影响**：本次 502 只持续约 4 秒且只影响一个公开接口；而 500 持续了整个下午，命中了用户的主流程。

---

## 四、本次已完成的修复

### 代码侧（本地仓库，**未提交**）

| 文件 | 改动 |
| --- | --- |
| `migrations/business/20261117_01_aibot_chat_up.sql` | 从 `migrations/aibot/` 迁入（原文件内容未改，git 识别为 rename） |
| `migrations/business/20261117_01_aibot_chat_down.sql` | **新增**（原缺失，删除三张表） |
| `migrations/business/20261117_01_aibot_chat_verify.sql` | **新增** |
| `migrations/business/20261117_01_aibot_chat_verify_previous.sql` | **新增** |
| `migrations/business/20261117_02_bot_intake_{up,down}.sql` | 从 `migrations/aibot/` 迁入（内容未改） |
| `migrations/business/20261117_02_bot_intake_verify.sql` | **新增** |
| `migrations/business/20261117_02_bot_intake_verify_previous.sql` | **新增** |
| `migrations/business/20261117_03_aibot_love_report_{up,down}.sql` | 从 `migrations/aibot/` 迁入（内容未改） |
| `migrations/business/20261117_03_aibot_love_report_verify.sql` | **新增** |
| `migrations/business/20261117_03_aibot_love_report_verify_previous.sql` | **新增** |
| `migrations/business/manifest.json` | 登记 3 个 version（含 up/down/verify/verify_previous 的 sha256），共 19 个版本 |
| `migrations/aibot/` | 整个目录删除（孤儿目录，无 runner 接管） |
| `database_setup_marriage.py` | 建表字典补入 4 张 aibot 表（全新库可直接建出） |
| `scripts/check_release_schema.py` | 门禁新增 5 项（4 张表 + 1 列） |

> 命名规范依据 `PROJECT_RULES.md` 7.2：每个 version 必须齐备 `up` / `down` / `verify` / `verify_previous` 四个文件并登记 sha256，runner 在 `_manifest()` 中逐个校验存在性与校验和。

### 生产侧（已执行）

| 动作 | 结果 |
| --- | --- |
| 备份 | `/home/ubuntu/backup_xuanshiai_20261118_152708.sql`（732,740 字节，含全部新建表与列） |
| 执行迁移 | `20261117_01/02/03` 三个 up 全部 OK（5 条语句） |
| 结构验证 | 4 张表 + `bot_intake_json` 列全部 EXISTS |
| 9 项发布门禁 | `GATE_FAILURES = 0` |
| 接口验证 | `/users/me/profile` → 200、`/ai/aibot/assignment` → 200（`agent_id=yuzhou`）、`/public/page-switches` → 200 |
| 日志验证 | 15:26 之后 5xx 归零，`/users/me/profile` 连续 14 次 200 |

---

## 五、仍需解决的基建缺口

| # | 缺口 | 影响 | 建议 |
| --- | --- | --- | --- |
| 1 | 迁移 runner 不支持 production | 生产迁移只能手工执行，易漏、易重复执行报 1060 | 给两个 runner 增加 `--target production`（带显式 `--allow-production` 确认位），或补一个生产迁移封装脚本 |
| 2 | 无部署脚本，流程全靠手工 | 本次事故的直接诱因 | 把本 SOP 第二节固化成 `scripts/deploy_production.sh` |
| 3 | nginx 单上游无冗余 | 每次重启都有 502 窗口 | 方案 A：双实例 + upstream + 健康检查 |
| 4 | 旧服务器部署脚本从未入库 | 新服务器无法继承真实发布机制 | `docs/待完成事项.md` DE-08 条目已登记，脱敏后入库 |

---

## 六、配套条目

- `docs/待完成事项.md`：`DEPLOY-SOP-20261118` 条目（本次新增）
- `docs/内容安全-微信图片违规检测接入方案-2026-11-18.md`：与本次事故无关，但共享「发布前门禁」这一基础设施
