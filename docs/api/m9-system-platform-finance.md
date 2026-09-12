# M9 系统管理 + 平台配置 + 财务 + 电子合同 — 后端契约文档

> 范围：36 页 UI 联调中的 9 🔴 完全静态页 + 5 🟡 核心 + 23 🟢 已接线。
> 本文聚焦 9 个 🔴 接通所需的 1 个新增端点 + 既有端点复用 + 2 个配置域字段扩展。
> 不新增任何业务表（沿用 `account_ledger` / `business_audit_log` / `matchmaker_admin_account` / `admin_config_snapshot`）。

---

## 一、功能分类（按前端页 → 后端归属）

### ✅ 已完成（本期零改动，23 页已接线）
- 系统管理 9：system-setting-basic / adconfig / outbound-call-platform / out-call-list / sms-group / system-setting-admin-user / -add / -group / -log
- 平台配置 7：platform-config-basic / navconfig / page / power-config / content / base / payconfig
- 公众号 5：wechat-config / menu / autoreply / template / send
- 小程序 1：miniprogram-config
- 电子合同 1：e-contract-yinzhang

### 🟡 需完善（本期改动，14 页）
**9 🔴 完全静态 → 全部接通**
| # | 前端页 | 配置域 / 端点 | 备注 |
|---|---|---|---|
| 1 | finance-config | `PATCH /admin/configs/finance` | 13 字段受控；UI 含 4 种提现方式 + 6 套充值套餐 |
| 2 | system-finance-order | `GET /admin/finance/orders` + `daily-report` | 27 分类 tab + 4 筛选 + 统计卡 |
| 3 | system-credit-history | `GET /admin/finance/ledger` + `POST /credit-grants` | 列表 + 发放积分弹窗 |
| 4 | system-cashout-history | `GET /admin/finance/withdrawals` + `PATCH /withdrawals/{id}` | 5 status tab + 已完成/拒绝 |
| 5 | finance-statistic | `GET /admin/finance/daily-report` | 4 tab/period + 按日聚合 |
| 6 | e-contract-config | `PATCH /admin/configs/econtract_config` | 总开关 + 4 项键值 |
| 7 | e-contract-template | `PATCH /admin/configs/econtract_templates` | 占位数据源 |
| 8 | e-contract-list | `PATCH /admin/configs/econtract_records` | 占位数据源 |
| 9 | system-setting-admin-user-edit | `GET/PATCH /admin/matchmaker/accounts/{id}` | 编辑具体账号；`?id=` 传参 |

**5 🟡 半接线 → 核心补全**
| # | 前端页 | 改动 |
|---|---|---|
| 10 | sms-signature | `readOnly`→可编辑 input + 保存按钮 → `PATCH /admin/configs/sys_sms.signature` |
| 11 | sms-notices | adminTags 选择器补 callback（拉 `/admin/matchmaker/accounts` 多选） |
| 12 | sms-record | 错误码/在线充值 保留 toast（依赖第三方） |
| 13 | wechat-fans | 同步公众号粉丝/搜索按钮保留 toast（依赖第三方） |
| 14 | out-call-record | 搜索/日期范围/导出保留 toast（依赖第三方） |

### ⚫ 废弃：无（36 页全部保留）

---

## 二、数据库变更

- **建表：无**（M8 风格，复用既有 `account_ledger` / `business_audit_log` / `matchmaker_admin_account` / `admin_config_snapshot` / `withdrawal_request`）。
- **补列：无**（`finance` 配置域在 `DEFAULT_CONFIGS` 加 13 字段；`econtract_config` 加 `enabled` 字段）。
- **索引**：复用既有索引（`account_ledger` 已有 `uk_account_ledger_key(idempotency_key)` + `idx_account_ledger_account`）。
- **种子**：`ensure_defaults` 自动合并（`finance` / `econtract_config` 命名空间历史快照会被 `deepMerge` 补缺失键）。

---

## 三、接口文档

### 3.1 新增 1 个端点

#### `POST /admin/finance/credit-grants`
- 用途：后台「积分明细-发放积分」弹窗提交
- 权限：`finance.write`
- Request Body：
  ```json
  {
    "target_type": "all" | "member" | "verified" | "matchmaker_team",
    "user_ids": [1, 2, 3],          // target_type=member 时必填；其余可省
    "amount": 100,                    // 每人发放积分（正整数，≤ 1,000,000）
    "reason": "春节红包"               // 必填，≤ 20 字
  }
  ```
- 行为：
  1. 按 `target_type` 解析账号集：
     - `all` → `users.status=1` 全量
     - `member` → `user_ids`（入参校验非空、≤ 5000）
     - `verified` → `users JOIN user_auth WHERE realname_status=2`
     - `matchmaker_team` → `user_role WHERE role_code IN ('service_matchmaker','promoter') AND status=1`
  2. 分批 500 → 写 `account_ledger(direction='CREDIT', state='AVAILABLE', source_type='admin_grant', source_id=admin.id, idempotency_key='credit-grant:<admin.id>:<user_id>:<amount>:<reason>')`
  3. 单条 `business_audit_log(actor_user_id, action='finance.credit_grant', resource_type='finance', reason, after_json={granted_count, amount_per_user, total_amount, ledger_ids[:10]})`
  4. 不写 `payment_order`、不写 `commission_entry`、不触发分账
- Response（201）：
  ```json
  {
    "granted_count": 50,
    "total_amount": 5000,
    "sample_ledger_ids": [101, 102, ...],
    "target_user_ids": [1, 2, 3, ...]
  }
  ```
- 错误码：
  - 401 未登录 / 403 缺 `finance.write` 权限
  - 422 amount≤0 / reason 空 / member 目标 user_ids 空 / 超过 5000
  - 404 解析账号集为空
  - 422 不支持的 target_type

### 3.2 复用既有端点（前端接通即可，后端不动）

| 前端页 | 端点 | 权限 | 备注 |
|---|---|---|---|
| finance-config | `GET/PATCH /admin/configs/finance` | `platform.config.read/write` | 扩 13 字段 |
| e-contract-config | `GET/PATCH /admin/configs/econtract_config` | `platform.config.read/write` | 加 `enabled` 字段 |
| e-contract-template | `GET/PATCH /admin/configs/econtract_templates` | `platform.config.read/write` | 占位 items 列表 |
| e-contract-list | `GET/PATCH /admin/configs/econtract_records` | `platform.config.read/write` | 占位 items 列表 |
| sms-signature | `PATCH /admin/configs/sys_sms` | `platform.config.write` | 改 signature 字段 |
| system-finance-order | `GET /admin/finance/orders` | `finance.read` | 分页 + 4 筛选 + 时间区间 |
| system-finance-order | `GET /admin/finance/daily-report` | `finance.read` | 按日聚合统计卡 |
| system-credit-history | `GET /admin/finance/ledger` | `finance.read` | account_type=user + source=admin_grant 筛选 |
| system-cashout-history | `GET /admin/finance/withdrawals` | `finance.read` | status 筛选 |
| system-cashout-history | `PATCH /admin/finance/withdrawals/{id}` | `finance.write` | status='APPROVED'/'REJECTED'/'SUCCEEDED' |
| finance-statistic | `GET /admin/finance/daily-report` | `finance.read` | 4 tab/period |
| system-setting-admin-user-edit | `GET /admin/matchmaker/accounts/{id}` | `matchmaker.account.manage` | 编辑前读 |
| system-setting-admin-user-edit | `PATCH /admin/matchmaker/accounts/{id}` | `matchmaker.account.manage` | 改分组/手机/姓名/状态 |
| system-setting-admin-user-edit | `POST /admin/matchmaker/accounts/{id}/reset-password` | `matchmaker.account.manage` | 密码重置 |
| system-setting-admin-user-edit | `GET /admin/matchmaker/accounts` | `matchmaker.account.manage` | 分组下拉（admin_groups） |

---

## 四、后端代码

### 4.1 schema（`app/schemas/finance.py`）
- 新增 `CreditGrantRequest` / `CreditGrantResult`：
  - `CreditGrantRequest.target_type: Literal["all","member","verified","matchmaker_team"]`
  - `user_ids: list[int] | None`（member 必填）
  - `amount: int (1~1_000_000)`
  - `reason: str (1~20 字)`
  - `model_validator(mode="after")` 校验 member 必须有 user_ids、≤ 5000

### 4.2 service（`app/services/finance.py`）
- 新增 `_resolve_credit_targets(db, target_type, user_ids) -> list[int]`：
  - 4 target_type 分支；不支持抛 422
- 新增 `admin_grant_credits(db, admin, request) -> CreditGrantResult`：
  - 解析账号集 → 分批 500 写 `account_ledger` → 单条 `business_audit_log`
  - 返回 `granted_count` / `total_amount` / `sample_ledger_ids[:10]` / `target_user_ids`

### 4.3 route（`app/api/routes/finance.py`）
- 在 `admin_router` 注册：
  ```python
  @admin_router.post("/credit-grants", response_model=CreditGrantResult, status_code=201, ...)
  async def grant_credits(body, admin, db):
      admin.require("finance.write")
      return await admin_grant_credits(db, _finance_actor(admin), body)
  ```
- 静态路径：`/admin/finance/credit-grants`（在所有 `/{id}` 动态段之前；与 `/withdrawals/{withdrawal_id}` 无冲突）

### 4.4 配置域字典（`app/services/admin_config.py`）
- `finance` 命名空间扩字段：
  ```python
  {
      "payment_mode": "mock",
      "payment_channels": [],
      "point_name": "金币",         # 新
      "point_ratio": 10,            # 新
      "balance_name": "余额",       # 新
      "withdrawal": {
          "enabled": False, "min_amount": "0.00",
          "fee_mode": "none",       # 新
          "fee_rate": 1,            # 新
          "fee_threshold": "100",   # 新
      },
      "withdraw_methods": {         # 新
          "auto_wechat":   {"enabled": False, "min_amount": "1", "max_amount": "500"},
          "manual_wechat": {"enabled": True,  "min_amount": "1", "max_amount": "1000"},
          "manual_bank":   {"enabled": True,  "min_amount": "1", "max_amount": "1000"},
          "manual_alipay": {"enabled": True,  "min_amount": "1", "max_amount": "1000"},
      },
      "recharge_packages": [        # 新（6 套）
          {"name": "积分充值套餐1", "amount": "1",   "points": 10},
          ...
      ],
      "refund": {"manual_review": True},
      "free_payment": {"enabled": False},
      "e_contract": {"enabled": False},
      "commission_rules": []
  }
  ```
- `econtract_config` 命名空间扩字段：
  ```python
  {"enabled": False,  # 新（前端腾讯电子签总开关）
   "items": [
       {"id": 1, "key": "sign_expire_days", ...},
       {"id": 2, "key": "expire_remind_days", ...},
       {"id": 3, "key": "default_contract_type", ...},
       {"id": 4, "key": "allow_revoke", ...},
   ]}
  ```
- `sensitive_keys`：`finance` 仍保留 `["merchant_key", "private_key", "public_key"]`；`econtract_config` 空数组。

---

## 五、联调注意

### 5.1 关键决策（与 M8 一致的"少即是多"原则）
1. **发放积分**：✅ 新建 `POST /admin/finance/credit-grants`（写 ledger+audit，target 4 类批量）；✖ 复用 `finance.orders`（语义错、对账混淆）
2. **finance-config 13 字段**：✅ 直接扩 `finance` 命名空间 DEFAULT_CONFIGS（单一提交）；✖ 新建 `finance_ui` 命名空间（表单割裂）
3. **23 🟢 装饰按钮**：✅ 本期彻底不动（防范围蔓延）；M9-二期再统一打装饰按钮补丁
4. **5 🟡 半接线**：✅ sms-signature/sms-notices adminTags 内部可补；其余 3 个依赖第三方平台保留 toast

### 5.2 踩坑 / 顺序 / 回归
- 命名空间字符串必须与 `DEFAULT_CONFIGS` 键**完全一致**（`finance` / `econtract_config` / `econtract_templates` / `econtract_records` / `sys_sms` / `wechat_mp_fans`）
- `ensure_defaults` 在 `get_config` 内自动跑，但 `DEFAULT_CONFIGS` 改后需**重启后端**才会注入新种子键
- 🔴 **关键坑**：system-setting-admin-user-edit 当前无账号 id 入口 → 改用 `?id=` 查询参数取账号 id（路径不变，满足"UI 不可动"），否则点编辑任意账号都失败
- `deepMerge` 对数组整体替换：前端保存 `finance` 须传**完整** `withdraw_methods` / `recharge_packages` 数组，否则丢配置
- `PATCH /admin/configs/econtract_config` 的 `items` 数组同理须传全量（保留默认 4 项）
- 顺序：先部署后端（字典+新端点）→ 再前端接通 → `tsc` 通过 → `pytest`
- 回归：`withdraw_policy(db)` 读 `withdrawal` 字段，扩 `finance` 字段时**勿改名**

### 5.3 本次不动边界
- 23 🟢 装饰按钮/纯 UI 控件不实现
- sms-record/wechat-fans/out-call-record 的同步/充值/错误码/导出保持 toast
- e-contract-template/list 保持占位（腾讯电子签接入前无真实数据）

### 5.4 P1 留尾项
1. adminTags 语义确认（系统管理员 vs 红娘后台账号，接口用 `/admin/matchmaker/accounts`）
2. credit-grant `matchmaker_team` 的 `account_type` 取值（当前统一 `user` + 实际 user_id）
3. system-cashout-history 导出 Excel 端点缺失 → 暂隐藏按钮
4. finance-statistic 按分类 tab 的后端聚合口径（当前 daily-report 仅按日）
5. e-contract 三页接入腾讯电子签后的真实 CRUD（下期）

---

## 六、自检报告

### 6.1 tsc
```
cd E:\HTML\xuanshiai-admin && node ./node_modules/typescript/bin/tsc --noEmit
# 预期：0 error
```

### 6.2 pytest
```
cd E:\houduan\xuanshiai && .venv/Scripts/python.exe -m pytest tests/test_m9_system_finance_ext.py -q
# 预期：11 passed
```
全量回归：`tests/` 总用例数（248+ passed）。

### 6.3 端点契约
```bash
# 1) 权限链
curl -X POST /api/v1/admin/finance/credit-grants  → 401（未登录）
curl -X POST /api/v1/admin/finance/credit-grants -H "Bearer <only-finance.read-token>" → 403（缺 finance.write）
curl -X POST /api/v1/admin/finance/credit-grants -H "Bearer <finance.write-token>" -d '{"target_type":"member","user_ids":[1],"amount":10,"reason":"测试"}' → 201

# 2) 配置域
curl /api/v1/admin/configs/finance  → 200，含 point_name/point_ratio/balance_name/withdraw_methods/recharge_packages
curl /api/v1/admin/configs/econtract_config → 200，含 enabled: false

# 3) 既有端点
curl /api/v1/admin/finance/orders  → 200
curl /api/v1/admin/finance/withdrawals  → 200
curl /api/v1/admin/finance/ledger?account_type=user  → 200
curl /api/v1/admin/finance/daily-report  → 200

# 4) 路径顺序
POST /admin/finance/credit-grants  在  /admin/finance/withdrawals/{withdrawal_id}  之前，无 422 冲突
```

### 6.4 路径顺序
- `/admin/finance/credit-grants`（静态）必须在 `/admin/finance/withdrawals/{withdrawal_id}`（动态）之前声明（FastAPI 按声明顺序匹配）→ 本次路由加在末尾（line 271+），无冲突
- e-contract 三页手写 breadcrumb 与 `breadcrumb-config.ts` 不一致 → 菜单高亮需联调首验

---

## 七、附：测试用例清单

`tests/test_m9_system_finance_ext.py`（11 用例）：
1. `test_m9_finance_defaults_have_extended_fields` — finance DEFAULT_CONFIGS 13 字段
2. `test_m9_econtract_config_has_enabled_flag` — econtract_config.enabled 默认 False
3. `test_m9_credit_grants_route_registered` — POST /credit-grants 注册
4. `test_m9_credit_grants_schema_validation` — member 缺 user_ids / amount≤0 422
5. `test_m9_credit_grants_resolve_targets_logic` — 4 target_type 分支
6. `test_m9_credit_grants_audit_logged` — 写 business_audit_log
7. `test_m9_credit_grants_uses_account_ledger` — INSERT account_ledger
8. `test_m9_credit_grants_batch_500` — 分批写入
9. `test_m9_existing_finance_endpoints_intact` — 既有 5 端点保留
10. `test_m9_config_endpoints_intact` — /admin/configs/{namespace} 保留
11. `test_m9_no_new_business_tables` — 不新增业务表
+ `test_m9_syntax_all_files` — 4 文件语法检查