# 属地解析数据文件（IP 库 / 手机号段表）

本目录存放「一键刷新 IP / 手机属地」功能所需的**外部数据资产**。代码只读取，不生成。

> 2026-11-04：主路径已改为阿里云 API（IP 云市场 AppCode、手机号码百科 AccessKey）；
> 本目录资产用于 `REGION_*_PROVIDER=offline` 或 API 失败时的 `REGION_*_FALLBACK_PROVIDER=offline` 兜底。

## 1. IP 库（`ip2region_v4.xdb`）

- 用途：IP → 省/市（开源数据上限为**市级**，不含区县）。
- 来源：https://github.com/lionsoul2014/ip2region （`data/ip2region_v4.xdb`，约 11MB）。
- 放置路径：`data/region/ip2region_v4.xdb`，或通过 `REGION_IP_DB_PATH` 指定绝对路径。
- **不进 git**：11MB 二进制每次更新都产生新 blob，用部署脚本下发并记录 sha256。
- 当前工作区放入的是官方 Gitee `v2.12.0` 的 `data/ip2region.xdb`（重命名为 `ip2region_v4.xdb`），字段为 5 段 `国家|区域|省份|城市|ISP`，城市下标 `2`。

### 还需要 Python 查询绑定

官方 binding 为单文件 `xdbSearcher.py`，二选一：

1. 放到 `app/services/region/vendor/xdbSearcher.py`（推荐，随代码走）；
2. 作为可导入模块安装（`import xdbSearcher` 可用即可）。

当前工作区使用官方 Gitee `v2.12.0` 的 `binding/python/xdbSearcher.py`，已放在
`app/services/region/vendor/xdbSearcher.py`；同目录保留 `LICENSE.md`。v3.x 的 Python
binding 已改为 `ip2region` 包结构，与本项目当前单文件导入方式不兼容，升级时需同步调整。

两者都缺时，接口按设计返回 **503**（`IP 库查询绑定未就绪`），不会写任何数据。

### 版本兼容

| 数据版本 | 字段格式（`|` 分隔） | `REGION_IP_CITY_INDEX` |
|---|---|---|
| 开源 3.x | `国家|省份|城市|ISP` | 2（默认） |
| 商用标准版 | `国家|省份|城市|区县|ISP|...` | 2 |
| 商用满载版 | `大洲|国家|省份|城市|区县|ISP|...` | **3** |

换数据文件时**必须先核对下标**，否则会静默取错字段（见方案 §2.1）。

## 2. 手机号段表（`phone_segment.csv`）

- 用途：手机号前 7 位 → 省/市。
- 格式：`prefix,province,city,isp,updated_at`（UTF-8，可带 BOM）。`prefix` 需为纯数字。
- 放置路径：`data/region/phone_segment.csv`，或通过 `REGION_PHONE_SEGMENT_PATH` 指定。
- 示例见 `phone_segment.sample.csv`（**仅演示格式，不是可用数据**）。
- **仓库只保留 sample**；真实号段表由运维下发并记录 sha256。数据许可允许时再评估是否提交 git，不要用 sample 冒充生产数据。

### 数据来源与更新

- 工信部码号资源按批次公示（约每 10 天 1–2 批），建议**月度**更新；
- 存量已分配号段的归属地不会因新批次变化，新批次影响的是新投放号段；
- 连续两次月度更新均未影响存量号码时，可降为季度，改由「未命中率 > 3% 告警」驱动；
- 虚拟运营商号段（162/165/167/170/171）需单独覆盖。

## 3. 配置项

```dotenv
REGION_RESOLVER_PROVIDER=offline        # disabled / mock / offline / real
REGION_IP_DB_PATH=/home/xuanshiai/data/region/ip2region_v4.xdb
REGION_PHONE_SEGMENT_PATH=/home/xuanshiai/data/region/phone_segment.csv
REGION_IP_CITY_INDEX=2
REGION_REFRESH_BATCH_SIZE=500
REGION_REFRESH_OVERWRITE_MAX_ROWS=5000
```

IP 与手机的就绪度**互相独立**：IP 库缺失时 `refresh-ip-region` 返回 503，
但 `refresh-phone-region` 仍可正常刷新。就绪状态可通过
`GET /api/v1/admin/matchmaker/members/region-resolver` 的
`ip_db_ready` / `phone_segment_ready` 查看。

## 4. 上线预检

```bash
cd /home/xuanshiai
.venv/bin/python scripts/region_preflight.py --expect-ready
```

脚本只读检查 provider、IP 库、`xdbSearcher` 绑定、号段表、阿里云 API 配置、`users` 相关列与候选数量，
任一未就绪时退出码为 2。**不发起第三方 API 请求**；`*_api_ready` 只代表配置字段完整，
凭证有效性需通过 canary 验证。输出中的 sha256 与号段表 `max_updated_at` 应记录到发布记录。
