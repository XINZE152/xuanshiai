# 会员 CRM 数据报表（M3-4）管理后台接口

> 本文档对应前端页面 `E:\HTML\xuanshiai-admin\src\app\(admin)\love-user-statistics\page.tsx`（菜单：会员CRM → 数据报表）。
> 本模块**不新增接口**，复用既有 `GET /api/v1/admin/member-statistics`，本次为**字段扩充 + 契约补全**。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 基础前缀 | `/api/v1` |
| 鉴权 | `Authorization: Bearer <access_token>`（红娘后台独立 Token），依赖函数 `get_current_matchmaker_admin` |
| 权限 | `matchmaker.member.read` **或** `dashboard.read` **任一即可**（该接口同时服务首页大盘与数据报表页） |
| 成功响应 | **不包 `data`**，直接返回业务对象 |
| 错误响应 | `HTTPException(status_code, detail)` → `{"detail": "..."}`，**无业务错误码字段** |
| 日期格式 | query 参数为 `YYYY-MM-DD`；返回项中的 `date` 为 `YYYY-MM-DD`，`label` 视维度而定 |
| 数值类型 | 全部为 **number**（整型计数 / 浮点）；本模块**无金额字段**，故不涉及 Decimal→string 序列化 |

### 1.1 与既有接口的关系

本接口 `GET /api/v1/admin/member-statistics` 在本期之前即已存在（供管理端首页大盘使用）。本次为满足「会员CRM → 数据报表」页 7 个 Tab 的完整 UI，做了三类**向后兼容**的扩充：

| 变更 | 说明 |
|---|---|
| `groups.growth` 增加 `male_count` / `female_count` | 「会员增长」Tab 需要男/女拆分 |
| `metrics` 增加 `max_daily_members` / `max_daily_date` / `max_monthly_members` / `max_monthly_month` | 页面 4 张指标卡需要「单日新增峰值」「单月新增峰值」 |
| `groups` 增加 `intention_report`；`user_partner_preference` 增加 `preferred_occupation` 列 | 「会员意向」Tab 固定 9 类标签；「择偶要求 → 职业」维度 |

> 既有消费者（首页大盘）不读取新增字段，故**不受影响**。

### 1.2 7 个 Tab 与返回字段的对应关系

| Tab | 取数字段 |
|---|---|
| 会员增长 | `metrics.*` + `groups.growth` |
| 会员跟进 | `groups.follow` + `totals.follow` + `groups.follow_report` |
| 会员意向 | `groups.intention` + `groups.intention_report` |
| 会员基本状况 | `groups.basic_groups.{gender,marriage,age,education,house,car,income,realname,occupation,hometown,residence,dating_status}` |
| 择偶要求 | `groups.requirements.{male,female}` + `groups.preference_labels` |
| 浏览统计 | `groups.browse` + `totals.browse` + `groups.browse_daily` + `groups.browse_report` |
| 人气统计 | `groups.popularity` + `totals.popularity` + `groups.popularity_female` + `groups.popularity_male` + `groups.apply_female` + `groups.apply_male` |

---

## 2. 查询会员 CRM 数据报表

### 2.1 `GET /api/v1/admin/member-statistics`

**基本信息**

| 项 | 内容 |
|---|---|
| 路径 | `/api/v1/admin/member-statistics` |
| 方法 | `GET` |
| 权限 | `matchmaker.member.read` 或 `dashboard.read` |
| 用途 | 一次性返回 7 个 Tab 所需的全部统计数据 |

**请求参数**

| 参数名 | 位置 | 类型 | 必填 | 默认值 | 校验 | 业务含义 |
|---|---|---|---|---|---|---|
| `from` | query | string | 否 | `to - 14 天` | `YYYY-MM-DD`，非法格式 422 | 统计区间起（含） |
| `to` | query | string | 否 | 服务器当天 | `YYYY-MM-DD`，非法格式 422 | 统计区间止（含，按自然日闭区间处理） |

> 注意：query 参数名是**别名 `from` / `to`**，不是 `from_date` / `to_date`。
> 区间实际以 `>= from 00:00:00 AND < to + 1 天` 的下推方式取数（左闭右开）。

**请求示例**

```
GET /api/v1/admin/member-statistics?from=2026-08-29&to=2026-09-11
Authorization: Bearer <access_token>
```

非法示例（`from` 格式错误 → 422）：

```
GET /api/v1/admin/member-statistics?from=2026/08/29
```

```json
{"detail": [{"type": "date_from_datetime_parsing", "loc": ["query", "from"], "msg": "Input should be a valid date or datetime, invalid date separator, expected `-`"}]}
```

**返回参数**

顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `from_date` | string | 实际生效的区间起（`YYYY-MM-DD`） |
| `to_date` | string | 实际生效的区间止（`YYYY-MM-DD`） |
| `groups` | object | 见下方分组明细 |
| `totals` | object | 各分布维度的合计值 |
| `metrics` | object | 顶部指标卡 |

`metrics`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_members` | number | 当前有效会员总数（`users.status = 1` 且在数据范围内） |
| `today_members` | number | `to_date` 当天新增会员数 |
| `max_daily_members` | number | **单日新增峰值**（全量口径，不受区间限制） |
| `max_daily_date` | string | 单日峰值对应日期（`YYYY-MM-DD`）；无数据为 `""` |
| `max_monthly_members` | number | **单月新增峰值**（全量口径） |
| `max_monthly_month` | string | 单月峰值对应月份（`YYYY-MM`）；无数据为 `""` |

`totals`

| 字段 | 类型 | 说明 |
|---|---|---|
| `follow` | number | `groups.follow` 各项 `value` 之和 |
| `intention` | number | `groups.intention` 之和 |
| `basic` | number | `groups.basic` 之和 |
| `requirement` | number | `groups.requirement` 之和 |
| `browse` | number | 区间内浏览记录总数 |
| `popularity` | number | `groups.popularity` 之和 |

`groups` — 通用分布数组统一为 `DistItem`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | string | 维度标签（如「本科」「未婚」「查看会员资料」） |
| `value` | number | 该标签的计数 |

| 字段 | 类型 | 说明 |
|---|---|---|
| `follow` | `DistItem[]` | 会员跟进状态分布 |
| `intention` | `DistItem[]` | 会员意向分布（客源线索意向） |
| `basic` | `DistItem[]` | 会员基本状况分布（性别维度，见 `basic_groups.gender`） |
| `requirement` | `DistItem[]` | 交友目标分布（`user_partner_preference.dating_goal`） |
| `browse` | `DistItem[]` | 浏览行为分布（查看自己 / 查看会员资料） |
| `popularity` | `DistItem[]` | 人气榜（被浏览次数 Top 8 会员昵称） |
| `basic_groups` | object | 12 个子维度，每个为 `DistItem[]`：`gender` / `marriage` / `age` / `education` / `house` / `car` / `income` / `realname` / `occupation` / `hometown` / `residence` / `dating_status` |
| `browse_daily` | `DistItem[]` | 按日浏览数（`label` 为 `YYYY-MM-DD`） |
| `popularity_female` | `DistItem[]` | 女性会员被浏览 Top 10 |
| `popularity_male` | `DistItem[]` | 男性会员被浏览 Top 10 |
| `apply_female` | `DistItem[]` | 女性会员被申请（牵线）Top 10 |
| `apply_male` | `DistItem[]` | 男性会员被申请（牵线）Top 10 |
| `growth` | `GrowthRow[]` | 见下方 |
| `follow_report` | `FollowReportRow[]` | 见下方 |
| `browse_report` | `BrowseReportRow[]` | 见下方 |
| `requirements` | object | `{ male: PreferenceReport, female: PreferenceReport }`，见下方 |
| `preference_labels` | object | 择偶要求各维度的中文标签：`{age, marriage, height, education, housing, smoking, drinking, goal, occupation}` |
| `intention_report` | `IntentionItem[]` | 客户意向固定 9 类，见下方 |

`groups.growth[]` — `GrowthRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | string | 日期 `YYYY-MM-DD` |
| `member_count` | number | 当日新增会员数 |
| `male_count` | number | 当日新增男性会员数 |
| `female_count` | number | 当日新增女性会员数 |
| `vip_count` | number | 当日新开通会员（`user_membership.status=1` 去重）数 |
| `apply_count` | number | 当日牵线申请数 |
| `failed_count` | number | 当日牵线失败数（`match_apply.status IN (2,3)`） |
| `success_count` | number | 当日牵线成功数（`match_apply.status = 1`） |

`groups.follow_report[]` — `FollowReportRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `matchmaker` | string | 红娘昵称；未分配为 `未分配`；无昵称回退 `红娘{id}` |
| `member_count` | number | 该红娘名下会员数（`resource_assignment.status=1` 去重） |
| `never_followed` | number | 从未跟进过的会员数 |
| `over_3_days` | number | 距最近跟进 > 3 天的会员数 |
| `over_7_days` | number | > 7 天 |
| `over_15_days` | number | > 15 天 |
| `over_30_days` | number | > 30 天 |
| `follow_count` | number | 累计跟进次数 |
| `month_follow_count` | number | 本月跟进次数 |

`groups.browse_report[]` — `BrowseReportRow`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | string | 日期 `YYYY-MM-DD` |
| `home_views` | number | **恒为 `0`** —— 数据表未持久化「首页访问」事件，无法区分（见 2.2 业务规则） |
| `profile_views` | number | 当日浏览记录总数（当前即全部已持久化浏览量） |
| `popular_member` | string | 当日被浏览最多的会员昵称；无数据为 `-` |

`groups.requirements.{male|female}` — `PreferenceReport`

| 字段 | 类型 | 说明 |
|---|---|---|
| `age` | `DistItem[]` | 期望年龄区间分布 |
| `marriage` | `DistItem[]` | 期望婚况分布 |
| `height` | `DistItem[]` | 期望身高区间分布 |
| `education` | `DistItem[]` | 期望学历分布 |
| `housing` | `DistItem[]` | 住房要求分布 |
| `smoking` | `DistItem[]` | 抽烟要求分布 |
| `drinking` | `DistItem[]` | 喝酒要求分布 |
| `goal` | `DistItem[]` | 结婚要求分布 |
| `occupation` | `DistItem[]` | **期望职业分布**（本次新增数据源 `user_partner_preference.preferred_occupation`） |

`groups.intention_report[]` — `IntentionItem`

| 字段 | 类型 | 说明 |
|---|---|---|
| `label` | string | 固定 9 类之一（见下） |
| `count` | number | 该类标签命中的客源线索数；缺失计 `0` |

固定 9 类（顺序固定，全部返回）：
`A类未接`、`B类初步沟通`、`C类深入沟通未缔结`、`D类待确定到店时间`、`E类已确定到店`、`F类预约需二邀`、`G类已到店未签约`、`I类签单`、`J类放弃资源`

> 数据源：`customer_lead_tag_relation JOIN customer_lead_tag`，按 `tag.name` 聚合；与固定目录匹配时**忽略空格差异**。

**返回示例**

```json
{
  "from_date": "2026-08-29",
  "to_date": "2026-09-11",
  "groups": {
    "follow": [{"label": "已跟进", "value": 128}, {"label": "未跟进", "value": 42}],
    "intention": [{"label": "A类未接", "value": 12}, {"label": "I类签单", "value": 5}],
    "basic": [{"label": "男", "value": 310}, {"label": "女", "value": 268}],
    "requirement": [{"label": "倾向结婚", "value": 402}, {"label": "未填写", "value": 176}],
    "browse": [{"label": "查看会员资料", "value": 5210}, {"label": "查看自己", "value": 33}],
    "popularity": [{"label": "小雨", "value": 218}],
    "basic_groups": {
      "gender": [{"label": "男", "value": 310}, {"label": "女", "value": 268}],
      "marriage": [{"label": "未婚", "value": 402}],
      "age": [{"label": "26岁-30岁", "value": 188}],
      "education": [{"label": "本科", "value": 246}],
      "house": [{"label": "有房", "value": 121}],
      "car": [{"label": "无车", "value": 233}],
      "income": [{"label": "8千-1万元", "value": 97}],
      "realname": [{"label": "已实名", "value": 455}],
      "occupation": [{"label": "教师", "value": 31}],
      "hometown": [{"label": "杭州", "value": 88}],
      "residence": [{"label": "杭州", "value": 102}],
      "dating_status": [{"label": "公开相亲", "value": 560}]
    },
    "browse_daily": [{"label": "2026-09-11", "value": 96}],
    "popularity_female": [{"label": "小雨", "value": 218}],
    "popularity_male": [{"label": "阿泽", "value": 176}],
    "apply_female": [{"label": "小雨", "value": 31}],
    "apply_male": [{"label": "阿泽", "value": 22}],
    "growth": [
      {"date": "2026-09-11", "member_count": 12, "male_count": 7, "female_count": 5,
       "vip_count": 3, "apply_count": 9, "failed_count": 2, "success_count": 4}
    ],
    "follow_report": [
      {"matchmaker": "芸希老师", "member_count": 46, "never_followed": 3, "over_3_days": 8,
       "over_7_days": 4, "over_15_days": 1, "over_30_days": 0,
       "follow_count": 210, "month_follow_count": 37}
    ],
    "browse_report": [
      {"date": "2026-09-11", "home_views": 0, "profile_views": 96, "popular_member": "小雨"}
    ],
    "requirements": {
      "male": {
        "age": [{"label": "26-30岁", "value": 88}],
        "marriage": [{"label": "未婚", "value": 140}],
        "height": [{"label": "160-170cm", "value": 76}],
        "education": [{"label": "本科", "value": 91}],
        "housing": [{"label": "不限", "value": 150}],
        "smoking": [{"label": "不抽烟", "value": 62}],
        "drinking": [{"label": "不限", "value": 130}],
        "goal": [{"label": "不限", "value": 120}],
        "occupation": [{"label": "教师", "value": 18}]
      },
      "female": {
        "age": [{"label": "不限", "value": 70}],
        "marriage": [{"label": "未婚", "value": 110}],
        "height": [{"label": "175cm以上", "value": 64}],
        "education": [{"label": "本科", "value": 80}],
        "housing": [{"label": "有房", "value": 58}],
        "smoking": [{"label": "不抽烟", "value": 55}],
        "drinking": [{"label": "不限", "value": 100}],
        "goal": [{"label": "不限", "value": 96}],
        "occupation": [{"label": "工程师", "value": 14}]
      }
    },
    "preference_labels": {
      "age": "年龄", "marriage": "婚况", "height": "身高", "education": "学历",
      "housing": "住房", "smoking": "抽烟", "drinking": "喝酒",
      "goal": "结婚要求", "occupation": "职业"
    },
    "intention_report": [
      {"label": "A类未接", "count": 12}, {"label": "B类初步沟通", "count": 9},
      {"label": "C类深入沟通未缔结", "count": 6}, {"label": "D类待确定到店时间", "count": 4},
      {"label": "E类已确定到店", "count": 3}, {"label": "F类预约需二邀", "count": 2},
      {"label": "G类已到店未签约", "count": 1}, {"label": "I类签单", "count": 5},
      {"label": "J类放弃资源", "count": 7}
    ]
  },
  "totals": {"follow": 170, "intention": 17, "basic": 578, "requirement": 578, "browse": 5243, "popularity": 218},
  "metrics": {
    "total_members": 578, "today_members": 12,
    "max_daily_members": 26, "max_daily_date": "2026-05-20",
    "max_monthly_members": 412, "max_monthly_month": "2026-03"
  }
}
```

**空数据示例**（区间内无任何数据时，所有数组为 `[]`，计数为 `0`，峰值字段为空串）

```json
{
  "from_date": "2026-09-10",
  "to_date": "2026-09-11",
  "groups": {
    "follow": [], "intention": [],
    "basic": [], "requirement": [], "browse": [], "popularity": [],
    "basic_groups": {
      "gender": [], "marriage": [], "age": [], "education": [], "house": [],
      "car": [], "income": [], "realname": [], "occupation": [],
      "hometown": [], "residence": [], "dating_status": []
    },
    "browse_daily": [],
    "popularity_female": [], "popularity_male": [],
    "apply_female": [], "apply_male": [],
    "growth": [], "follow_report": [], "browse_report": [],
    "requirements": {
      "male": {"age": [], "marriage": [], "height": [], "education": [], "housing": [], "smoking": [], "drinking": [], "goal": [], "occupation": []},
      "female": {"age": [], "marriage": [], "height": [], "education": [], "housing": [], "smoking": [], "drinking": [], "goal": [], "occupation": []}
    },
    "preference_labels": {
      "age": "年龄", "marriage": "婚况", "height": "身高", "education": "学历",
      "housing": "住房", "smoking": "抽烟", "drinking": "喝酒",
      "goal": "结婚要求", "occupation": "职业"
    },
    "intention_report": [
      {"label": "A类未接", "count": 0}, {"label": "B类初步沟通", "count": 0},
      {"label": "C类深入沟通未缔结", "count": 0}, {"label": "D类待确定到店时间", "count": 0},
      {"label": "E类已确定到店", "count": 0}, {"label": "F类预约需二邀", "count": 0},
      {"label": "G类已到店未签约", "count": 0}, {"label": "I类签单", "count": 0},
      {"label": "J类放弃资源", "count": 0}
    ]
  },
  "totals": {"follow": 0, "intention": 0, "basic": 0, "requirement": 0, "browse": 0, "popularity": 0},
  "metrics": {
    "total_members": 0, "today_members": 0,
    "max_daily_members": 0, "max_daily_date": "",
    "max_monthly_members": 0, "max_monthly_month": ""
  }
}
```

### 2.2 使用方法与业务规则

**前置条件**

1. 请求须带有效红娘后台 Token；Token 失效/缺失 → 401。
2. 当前账号权限集合须含 `matchmaker.member.read` 或 `dashboard.read`（或通配 `*`），否则 403。

**调用顺序**

1. 页面挂载或日期范围变更时调用一次即可获得全部 7 个 Tab 的数据（**单接口聚合**，不需要分 7 次请求）。
2. Tab 切换纯前端行为，**不重新请求**。
3. 日期范围变更 → 携带 `from` / `to` 重新请求。

**口径说明（重要）**

| 规则 | 说明 |
|---|---|
| 区间 | `from` / `to` 均为自然日；数据按 `>= from AND < to + 1 天` 取。省略时默认最近 15 天（`to - 14` 至 `to`）。 |
| 有效会员口径 | 除峰值外的统计均限定在「有效会员」范围内：`users.status = 1` 且排除内部/系统账号，并受当前登录账号的数据权限（`SELF` / `STORE` / `ORGANIZATION` / `ALL`）约束。 |
| 峰值口径 | `metrics.max_daily_members` / `max_monthly_members` 为**全量历史口径**，不受 `from` / `to` 限制，与前端排名卡语义一致。 |
| `home_views` | **恒为 `0`**。数据层未持久化「首页访问」独立事件，`profile_views` 即全部已持久化浏览量。前端不应把它当作真实首页 PV 展示为有效指标。 |
| `intention_report` | 永远返回固定 9 类（顺序固定），无数据的类别 `count = 0`，**不省略条目**。标签匹配忽略空格差异。 |
| `popularity*` / `apply_*` | 榜单类，`popularity` 取 Top 8，`popularity_female` / `popularity_male` / `apply_female` / `apply_male` 取 Top 10。 |
| 数据权限 | 返回的所有聚合均按当前账号的数据范围过滤；`SELF` 范围下数字会显著偏小，属预期行为。 |
| 幂等 | 纯读接口，幂等，无副作用，无限流。 |

**关联数据源**

`users`、`user_profile`、`user_auth`、`user_partner_preference`、`user_browse_history`、`user_membership`、`match_apply`、`member_follow_up`、`resource_assignment`、`customer_lead`、`customer_lead_tag`、`customer_lead_tag_relation`。

### 2.3 错误

| HTTP | 触发条件 | 前端处理建议 | 错误响应 JSON |
|---|---|---|---|
| 401 | 未带 Token / Token 失效 | 由 `adminApi` 统一清 Token 并跳 `/login` | `{"detail": "未认证"}` |
| 403 | 权限集合不含 `matchmaker.member.read` 与 `dashboard.read` | 提示「无数据报表查看权限」，隐藏页面内容 | `{"detail": "缺少权限: matchmaker.member.read"}` |
| 422 | `from` / `to` 日期格式非法 | 提示「日期格式不正确」并回滚筛选 | `{"detail": [{"loc": ["query", "from"], "msg": "Input should be a valid date"}]}` |
| 500 | 服务端异常（如数据源表缺失） | `showConfigToast(Error.message, "error")`，页面展示错误态 | `{"detail": "Internal Server Error"}` |

### 2.4 文档完成自检清单

- [x] 基本信息（路径 / 方法 / 权限 / 用途）
- [x] 请求参数表（参数名、位置、类型、必填、默认值、校验、业务含义）
- [x] 请求示例 + **至少一个非法示例**
- [x] 返回参数表（嵌套全部展开，每个字段有业务含义）
- [x] 返回示例 + **空数据示例**
- [x] 使用方法与业务规则（前置条件 / 调用顺序 / 幂等 / 状态流转 / 边界场景）
- [x] 错误表（状态码 + 触发条件 + 前端处理建议 + 响应 JSON）

---

## 3. 关联数据库变更

本次为支撑「择偶要求 → 职业」维度，给 `user_partner_preference` 增加一列（建表脚本 + 幂等补列，均已在 `database_setup_marriage.py` 落盘）：

```sql
ALTER TABLE `user_partner_preference`
  ADD COLUMN `preferred_occupation` varchar(64) DEFAULT NULL COMMENT '期望职业';
```

其余字段全部复用已有表与已有列，**无其他数据库变更**。

---

## 4. 变更记录

| 项 | 变更前 | 变更后 | 影响范围 |
|---|---|---|---|
| `groups.growth[]` | 仅 `date / member_count / vip_count / apply_count / failed_count / success_count` | 增加 `male_count` / `female_count` | 新增字段，既有消费者不受影响 |
| `metrics` | 仅 `total_members` / `today_members` | 增加 `max_daily_members` / `max_daily_date` / `max_monthly_members` / `max_monthly_month` | 新增字段 |
| `groups.intention_report` | 不存在 | 新增，固定 9 类客户意向标签计数 | 新增字段 |
| `groups.requirements.*.occupation` | 不存在 | 新增「期望职业」分布 | 新增数据源 `user_partner_preference.preferred_occupation`（同时补列） |
| `groups.requirements.*.goal` 口径 | `preference.dating_goal`（NULL 显示 `不限`） | 保持不变 | — |
| `groups.basic_groups.realname` 口径 | `users.is_real_name = 1 OR auth.realname_status = 1`（把「认证中」误判为已实名） | `users.is_real_name = 1 OR auth.realname_status = 2` | 修正统计口径；「已实名」数会**下降**，属纠错 |
| `GET /api/v1/admin/member-statistics` 权限 | `dashboard.read` | `matchmaker.member.read` 或 `dashboard.read` 任一 | 数据报表页无需再申请大盘权限 |
| 文档 | — | 本文档新建 | — |
