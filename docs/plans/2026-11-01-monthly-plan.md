# 宣誓爱 2026 年 11 月开发计划（红娘工作台收口月）

> 编制时间：2026-11-01。依据：三仓库当前代码实况 + 10-30/10-31 两天已完成事项 + `待完成事项.md` §九 记录规则。
> 本月只认一个目标：**把「能点、能看、但是假」的东西全部变成真的**，并把「每次拉上游就复发」的两类回归固定成流程。不做新的版式设计。

---

## 一、月初盘点（带出处，不是感觉）

### A. 小程序仍是假数据或占位（10 处）

| # | 现状 | 出处 |
| --- | --- | --- |
| A1 | 管理中心 4 项业务指标（待我牵线/牵线成功/约见申请/等待见面）后端返回全 0 时，前端**静默替换**成 3/29/2/1 | `xuanshiai-vue/components/MatchmakerManagementCenter.uvue:138-156` |
| A2 | 看板初始值写死 ￥1,871.40 / ￥10,820.00 | 同文件 `:130-131` |
| A3 | 分成费率仍是 mock 数组 `mockMatchmakerCommissionRates` | `xuanshiai-vue/pagesSub/matchmaker/become-matchmaker.uvue:1275` |
| A4 | 提示语仍写「线下业绩与开单收款为本地演示数据」 | 同文件 `:1366` |
| A5 | 演示会员点联系方式被拒（3 处分支） | 同文件 `:2783`、`:2809`、`:2847` |
| A6 | 活动报名为本地演示 | 同文件 `:1462` |
| A7 | 录入表单「示例数据一键填充」 | 同文件 `:2101` |
| A8 | VIP 标签两个页签「服务端暂未开放」 | `xuanshiai-vue/components/MatchmakerPendingReview.uvue:499` |
| A9 | 排行榜「入驻中」提示 | `xuanshiai-vue/pagesSub/matchmaker/rank.uvue:22`、`:74` |
| A10 | 喜讯中心「送祝福」只弹 toast；后端 `blessing`/`blessing_count` 只从 extra JSON 读，无写接口 | `become-matchmaker.uvue:3224` / `xuanshiai-server/app/services/matchmaker_workspace.py:3081` |
| A11 | 锦旗只前端 canvas 生成、不写库；`GET /workbench/good-news/pennants` 只读无 POST | `become-matchmaker.uvue:816` / `xuanshiai-server/app/api/routes/matchmaker_workspace.py:637` |

### B. 后台死控件（点了报错或没反应）

| # | 现状 | 出处 |
| --- | --- | --- |
| B1 | **批量打标签必然 422**：前端 POST `tags`，schema 已按「客源标签功能 2026-10-27 下线」硬拒 | `xuanshiai-admin/src/app/(admin)/love-customer-list/page.tsx:1047` / `xuanshiai-server/app/schemas/customer_lead_admin.py:111` |
| B2 | 标签字典抽屉调的 3 个接口（`admin/customer-leads/tags`、`/{id}`、`/reorder`）**后端根本没有路由** | `src/lib/admin-endpoints.ts:3744` / `components/LeadDictDrawer.tsx:168` |
| B3 | 账号管理「删除」「批量删除」只弹「该功能暂未开放」 | `src/app/(admin)/reg-user-all/page.tsx:512`、`:526` |
| B4 | 其余 toast 级死控件：互选列表年收入下拉、系统反馈工具栏、系统赋能 | `mutual-selection-list/page.tsx:509`、`system-feedback/page.tsx:210`、`system-empower/page.tsx:213` |

> 更正一条旧账：**客源线索的 删除 / 批量删除 / 改派 三条已经是活的**，之前被列为待办是错的。证据：`love-customer-list/page.tsx:1003`、`:1021`、`:1041` → `customer_leads_admin.py:456`（assignment）、`:501`（batch-delete）、`:535`（DELETE /{lead_id}）。`PATCH /{lead_id}/assignment` 的「整行覆盖」陷阱仍需验证，但不属于死控件。

### C. 数据质量卡住功能

| # | 现状 | 出处 |
| --- | --- | --- |
| C1 | 2026-09 种子牵线行 `organization_id` 全 NULL → 门店超级红娘查 0 条，界面回落到演示卡；回补脚本已写、本机已跑，**测试/生产库未执行** | `xuanshiai-server/scripts/backfill_introduction_organization.py:1-13`、`app/services/matchmaker_workspace.py:247-255` |
| C2 | 后台建档/审核通过不写 `matchmaker_workspace_profile` → 小程序「修改红娘」选不到新建红娘；已回补 21/22 两个账号，历史账号与增量口径待复验 | `scripts/backfill_matchmaker_workspace_profile.py:1-13` |
| C3 | `matchmaker_introduction.matchmaker_id` NOT NULL，只有门店归属的申请人必须写占位红娘 | `app/services/matchmaker_workspace.py:2816` |
| C4 | `users.source` 列缺失 → 后台「来源分布」恒空表 | `app/api/routes/matchmaker_crm_admin.py:661` |
| C5 | 菜单权限种子只给了 `*`，普通管理员账号 403 | `database_setup_marriage.py:929` |
| C6 | 2026-09-06 种子账号缺 `user_matchmaker_apply`，被工作台名单查询排除 | `app/services/matchmaker_workspace.py:258-269` |

### D. 后端有路由、前端没人调（本月逐个定性，不擅自删）

`POST /admin/customer-leads/{id}/auto-assign`、`POST /admin/customer-leads/ai-extract`、`POST`+`DELETE /admin/matchmaker/members/{id}/wechat-binding`、`GET /matchmakers/{id}/ratings`、`contact-exchanges` 4 条（C 端只调纸飞机变体）。出处：`customer_leads_admin.py:464`、`:132`、`matchmaker_crm_admin.py:916`、`:976`、`matchmaker.py:85`、`:236`、`:246`、`:256`、`:265`。

### E. 流程性回归（每月必复发，写进固定动作）

1. 每拉一次 `xuanshiai-vue` 上游，`api/config.uts` 的 `API_BASE_URL` 就被冲回 `xhztest.xyz`（10-27、10-31 已两次中招）。
2. 全量 pytest 天然 31 failed / 204 errors，没有基线快照就分不清是谁改坏的；`tests/` 下与 matchmaker/lead/introduction 相关的测试文件共 **36 个**，本月只看这 36 个的相对结果。
3. `business_schema` 只建表不改列，补列必须登记 `database_setup_marriage._ensure_*`，漂移裁判是 `schema_contract check-db`（有 6 列 / 4 索引既有红项，别当成新坏）。

---

## 二、周排期

### W1　11-02 ~ 11-08　先止血：用户点了会报错的全部收掉

| 事项 | 做法 | 估时 |
| --- | --- | --- |
| B1 批量打标签 | 默认走「按钮置灰 + 说明文案（标签功能已于 2026-10-27 下线）」；只有产品明确要复活 tags 才改后端 | 0.5 天 |
| B2 标签字典抽屉 | 二选一：补 3 条后端路由（含 up/down SQL 与权限码），或撤掉入口并从 `admin-endpoints.ts` 删死调用 | 2 天 |
| B3 账号管理删除 | 接已有会员删除接口（软删 + 审计），或明确下线并从页面移除 | 1 天 |
| C1 牵线 organization_id 回补 | 在测试库执行脚本 → 复验门店超级红娘列表返回真实行数 | 1 天 |
| 上游合并固定动作 | 见 §三 | 0.5 天 |

**验收**：B1~B3 三处 0 报错复现；用门店红娘账号登录，牵线记录返回真实条数（回复里带数字）。

### W2　11-09 ~ 11-15　演示数据清零（第一批）：看板与费率

| 事项 | 做法 | 估时 |
| --- | --- | --- |
| A1/A2 看板静默覆盖 | 删除 `validationMetricValues` 覆盖分支，后端缺数就显示 0 + 「暂无数据」；`defaultMetrics` 里 ￥1,871.40 / ￥10,820.00 初始值改回 '0' | 1 天 |
| A3 分成费率 | 后端补只读费率接口（读分成配置表，按身份 level 取），前端去掉 `mockMatchmakerCommissionRates` | 2 天 |
| A4/A5 演示文案与分支 | 逐条清理「本地演示数据」提示与拒绝分支 | 1 天 |
| A6 活动报名 | 接已有活动接口；若产品口径是「本期不做」，写进 `待完成事项.md` 冻结原因 | 1 天 |
| A7 示例填充 | **保留**，但加「示例」角标（这是给红娘演示用的，不是假数据） | 0.5 天 |

**验收**：把测试库业务记录清空后，看板 8 项全为 0、无任何假数；真机截图 + 数字。

### W3　11-16 ~ 11-22　补闭环：喜讯写入、锦旗、录入两步走

| 事项 | 做法 | 估时 |
| --- | --- | --- |
| A10 送祝福 | `good_news` 增加祝福写入接口（内容 + 计数，走审核与敏感词），前端从 toast 占位改真调用 | 2 天 |
| A11 锦旗入库 | 需产品一句话：维持「只前端生成不写库」就冻结并记录；要写库则加表 + up/down/verify SQL + 后台锦旗墙审核 | 0.5 天决策 / 3 天实施 |
| 录入两步走（挂起任务 #39/#40） | 录入会员改「建号 + 跳资料编辑」，配套婚况 1/2/3 口径文档与线上复测 | 2 天 |
| 复制资料弹预览（#54，仍 in_progress） | 资料待审侧收尾，与客源线索侧同模板 | 1 天 |

**验收**：三条链路各跑通一条真实记录（含后台审核状态流转截图）。

### W4　11-23 ~ 11-29　后台与数据口径

| 事项 | 做法 | 估时 |
| --- | --- | --- |
| C4 来源分布 | 补 `users.source`（登记 `_ensure_*`）→ 后台图表接真 → `check-db` 复验 | 1.5 天 |
| C5 菜单权限种子 | 按角色映射替换 `*`，用普通管理员账号复验 403 消失 | 1.5 天 |
| C2/C6 红娘档案与种子账号 | 增量口径复验（新建红娘建档后立刻可被「修改红娘」选中），历史账号按需回补 | 1 天 |
| D 无调用方路由 | 逐条定性：接线 / 标注预留并写文档 / 走 §2.3 删除接口记录 | 1 天 |
| A8/A9 VIP 标签、排行榜入驻中 | 产品确认本期是否做；不做就写明冻结原因 | 0.5 天 |
| 11-30 收尾 | `admin后台接口文档.md`、`docs/api/*`、`待完成事项.md` 同步（只标状态、不删条目） | 1 天 |

---

## 三、每周固定动作（雷打不动）

1. 周五：三仓库各先 `chore(快照)` 存本地未提交改动，再 merge 上游（admin 只 merge 不 pull；vue/server 走本地合并）。
2. 合并后**第一件事**检查 `xuanshiai-vue/api/config.uts` 的 `API_BASE_URL`，被冲回 `xhztest.xyz` 就立刻改回；该文件永不提交。
3. 重启后端 :8000 与后台 :3001，跑 §一E 的 36 个测试文件，与上周相对结果比对。
4. 涉及列变更：登记 `database_setup_marriage._ensure_*` + 跑 `schema_contract check-db`。
5. 提交信息中文带作用域，按关注点拆分（例：`fix(客源线索): …` / `feat(红娘工作台): …`）；不 push（GitHub 侧只读，走 PR）。

---

## 四、风险与依赖

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 上游节奏不可控 | 10-31 一次吞了 admin 666 个提交，任何排期都要按「每周预留 0.5~1 天合并」估 | 排期已含固定动作行 |
| 测试基线本身是红的 | 全量 pytest 天然 31 failed / 204 errors，含上游带来的 discovery 额度等红项 | 只比对相关 36 个文件，不追全量绿 |
| 产品口径未冻结 | 锦旗是否入库、VIP 标签、tags 是否复活、排行榜入驻数据 | 每项只等一句话；W3/W4 若没答复则顺延并记录 |
| 真机与外部账号依赖 | 支付结果回调、TRTC 直播、实名、提现 | 本月只登记不承诺 |
| 单库环境 | 本机 MySQL 是便携版 8.0.42（`xuanshiai_runtime`），系统服务 MySQL84 是抢 3306 的假目标 | 验证前先确认连的是哪个实例 |

## 五、本月明确不做

直播相亲 R0.2 媒体闭环与 Outbox 多实例验收；AI 模块生产开闸（合规批准 / Provider 批准 / 保留期策略三道门禁未过，保持 `AI_FEATURE_DISABLED`）；支付与提现链路改造；新的页面版式与视觉设计（本月只收口，不再重排卡片）。

## 六、交付物清单

| 交付物 | 仓库 | 验收方式 |
| --- | --- | --- |
| 后台 3 处报错/死控件修复 | admin + server | 手工复现路径 + 截图 |
| 标签字典接口（或入口下线） | admin + server | 接口文档 + 契约测试 |
| 看板与费率去演示化 | vue + server | 清空业务数据后看板全 0 |
| 祝福写入接口 | server + vue | 一条真机祝福记录进待审 |
| 录入两步走 | vue | 线上复测：建号后跳编辑、保存即进待审 |
| `users.source` 与菜单权限种子修复 | server | `check-db` 无新增红项 + 普通管理员可进菜单 |
| 月末文档同步 | server `docs/` | `待完成事项.md` 状态更新，条目不删 |
