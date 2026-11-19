"""会员 CRM 列表筛选条件构造（会员资料页 `GET /admin/matchmaker/members`）。

与路由解耦，便于单测直接覆盖枚举映射、多值展开、日期边界和缺列降级；路由只负责
参数校验与数据范围收口。

缺列降级：本模块只产出 SQL 片段与绑定参数，是否引用某列由调用方传入的
`available` 列集合决定。迁移（migrations/filters/20261011_01）未执行时对应筛选
会被静默跳过而不是拼出 Unknown column，保证「先发版后迁移」不会让整页 500。
"""

from __future__ import annotations

from typing import Any

# 会员编号口径与线下VIP关键词搜索一致：G + 会员ID 左补零到 6 位。
MEMBER_CODE_EXPR = "CONCAT('G', LPAD(u.id, 6, '0'))"

# 搜索方式 -> 命中列。`smart` 保持原有「智能搜索」行为（昵称 / 手机 / ID / 编号）。
SEARCH_TYPE_SQL: dict[str, str] = {
    "smart": (
        "(u.nickname LIKE CONCAT('%', :search, '%') OR u.phone LIKE CONCAT('%', :search, '%')"
        " OR u.id = :search_id OR {code} = :search)"
    ),
    "phone": "u.phone LIKE CONCAT('%', :search, '%')",
    "wechat": "p.wechat LIKE CONCAT('%', :search, '%')",
    "code": "{code} LIKE CONCAT('%', :search, '%')",
    "id": "u.id = :search_id",
    "nickname": "u.nickname LIKE CONCAT('%', :search, '%')",
    "realname": (
        "EXISTS (SELECT 1 FROM user_auth sa WHERE sa.user_id = u.id"
        " AND sa.real_name LIKE CONCAT('%', :search, '%'))"
    ),
}

# 线上行为类型 -> EXISTS 谓词（u 为主表 users 的别名）。
BEHAVIOR_TYPE_SQL: dict[str, str] = {
    "viewed": "EXISTS (SELECT 1 FROM user_browse_history bh WHERE bh.user_id = u.id)",
    "viewed_by": "EXISTS (SELECT 1 FROM user_browse_history bh WHERE bh.target_user_id = u.id)",
    "favorited": "EXISTS (SELECT 1 FROM user_favorite bv WHERE bv.user_id = u.id AND bv.type = 2)",
    # user_boost 复用承载「置顶助推」（user_id = target_user_id = 自己），
    # 两个方向都必须排除自指记录，否则「买过置顶」会被筛成「爆过灯 / 被爆过灯」。
    "boosted": "EXISTS (SELECT 1 FROM user_boost bo WHERE bo.user_id = u.id AND bo.user_id <> bo.target_user_id)",
    "boosted_by": "EXISTS (SELECT 1 FROM user_boost bo WHERE bo.target_user_id = u.id AND bo.user_id <> bo.target_user_id)",
    "gift_sent": "EXISTS (SELECT 1 FROM user_gift_record gr WHERE gr.user_id = u.id)",
    "gift_received": "EXISTS (SELECT 1 FROM user_gift_record gr WHERE gr.target_user_id = u.id)",
}

# 证件/户籍地区 -> users.region 取值。
REGION_VALUES: frozenset[str] = frozenset({"mainland", "hmt", "overseas"})

# 余额口径（A12-钱包）：account_ledger 中 account_type='user' 的可用余额（ AVAILABLE），
# 与 finance.get_balance 的 available_amount 完全一致（CREDIT 减 DEBIT，REVERSED 不计）。
# 只读展示/排序，不新建表；account_ledger 已存在，故无缺列降级问题。
MEMBER_BALANCE_EXPR = (
    "COALESCE((SELECT SUM(CASE WHEN al.direction = 'CREDIT' THEN al.amount"
    " WHEN al.direction = 'DEBIT' THEN -al.amount ELSE 0 END)"
    " FROM account_ledger al WHERE al.account_type = 'user'"
    " AND al.account_id = u.id AND al.state = 'AVAILABLE'), 0)"
)

# 积分口径（A12）：取 user_points 中该用户最新一条 balance（与会员列表 `points` 展示列同源）。
# user_points 表已存在、无需新列，排序与展示复用同一表达式。
MEMBER_POINTS_EXPR = (
    "COALESCE((SELECT up.balance FROM user_points up"
    " WHERE up.user_id = u.id ORDER BY up.id DESC LIMIT 1), 0)"
)

# 排序白名单：值全部由服务端写死，不拼接用户输入。
SORT_SQL: dict[str, str] = {
    "created_at": "u.created_at DESC, u.id DESC",
    "entered_at": "u.created_at DESC, u.id DESC",
    "last_login_at": "COALESCE(u.last_login_at, '1970-01-01') DESC, u.id DESC",
    "last_follow_at": "COALESCE(f.last_follow_at, '1970-01-01') DESC, u.id DESC",
    "next_follow_at": "COALESCE(f.next_follow_at, '9999-12-31') ASC, u.id DESC",
    "assigned_at": "COALESCE(a.assigned_at, '1970-01-01') DESC, u.id DESC",
    "fetched_at": "COALESCE(u.fetched_at, '1970-01-01') DESC, u.id DESC",
    "balance": f"{MEMBER_BALANCE_EXPR} DESC, u.id DESC",
    # 积分排序：前端列表用 `integral`、展示字段名是 `points`，两个 token 同一口径都受理。
    "points": f"{MEMBER_POINTS_EXPR} DESC, u.id DESC",
    "integral": f"{MEMBER_POINTS_EXPR} DESC, u.id DESC",
    "id": "u.id DESC",
}

# 记录排序（order）与 sort_by 互斥；同时传时 order 优先。
ORDER_SQL: dict[str, str] = {
    "latest": "u.id DESC",
    "earliest": "u.id ASC",
    "smart": "u.created_at DESC, u.id DESC",
}


def split_values(raw: str | None, limit: int = 50) -> list[str]:
    """把「多选」参数按英文逗号拆成去重后的取值列表，空值返回空列表。"""
    if not raw:
        return []
    return [item.strip() for item in dict.fromkeys(raw.split(",")) if item.strip()][:limit]


def _placeholders(prefix: str, values: list[str], params: dict[str, Any]) -> str:
    """为多值 IN 生成命名占位符并写回 params，避免拼接用户输入。"""
    names = []
    for index, value in enumerate(values):
        key = f"{prefix}_{index}"
        params[key] = value
        names.append(f":{key}")
    return ", ".join(names)


def build_member_filter(
    values: dict[str, Any],
    available: frozenset[tuple[str, str]],
) -> tuple[str, dict[str, Any]]:
    """按传入的筛选值构造 WHERE 片段与绑定参数。

    `values` 的键名与接口的 query 参数同名；未传或为 None 的键表示该条件不生效。
    `available` 为 `(表名, 列名)` 集合，用于缺列降级。
    """
    where: list[str] = ["1=1"]
    params: dict[str, Any] = {}

    def column(table: str, name: str) -> bool:
        return (table, name) in available

    # ---- 搜索 ----
    search = values.get("search")
    if search:
        search_type = values.get("search_type") or "smart"
        sql = SEARCH_TYPE_SQL.get(search_type, SEARCH_TYPE_SQL["smart"]).format(code=MEMBER_CODE_EXPR)
        where.append(sql)
        params["search"] = search
        if search.isdigit():
            params["search_id"] = int(search)
        else:
            params["search_id"] = -1

    # ---- 基础枚举 ----
    if values.get("gender") is not None:
        where.append("u.gender = :gender")
        params["gender"] = values["gender"]
    if values.get("status") is not None:
        where.append("u.status = :status")
        params["status"] = values["status"]
    if values.get("vip") is True:
        where.append("v.user_id IS NOT NULL AND (v.vip_end_at IS NULL OR v.vip_end_at > UTC_TIMESTAMP())")
    elif values.get("vip") is False:
        where.append("(v.user_id IS NULL OR (v.vip_end_at IS NOT NULL AND v.vip_end_at <= UTC_TIMESTAMP()))")
    auth_status_raw = values.get("auth_status")
    profile_review = values.get("profile_review_status")
    if profile_review is not None:
        where.append("COALESCE(mr.status, 'PENDING') = :profile_review_status")
        params["profile_review_status"] = {0: "PENDING", 1: "REJECTED", 2: "PASSED", 3: "STORE_VERIFIED"}[profile_review]
    if auth_status_raw is not None and str(auth_status_raw) != "":
        # B6：支持英文逗号分隔多值（如 1,2,3,4 表示已提交资料），与 marriage/education
        # 等多选参数同口径；单个值时行为与旧的等值匹配一致，向后兼容。
        statuses = [int(item) for item in split_values(str(auth_status_raw)) if item.isdigit()]
        if statuses:
            names = []
            for index, status_value in enumerate(dict.fromkeys(statuses)):
                key = f"auth_status_{index}"
                params[key] = status_value
                names.append(f":{key}")
            where.append(f"COALESCE(ua.auth_status, 0) IN ({', '.join(names)})")
    if values.get("assigned") is True:
        where.append("a.matchmaker_id IS NOT NULL")
    elif values.get("assigned") is False:
        where.append("a.matchmaker_id IS NULL")
    follow_state = values.get("follow_state")
    if follow_state == "never":
        where.append("f.last_follow_at IS NULL")
    elif follow_state == "due_today":
        where.append("f.next_follow_at >= CURDATE() AND f.next_follow_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)")
    elif follow_state == "overdue":
        where.append("f.next_follow_at < CURDATE()")

    # ---- 新增筛选项 ----
    if values.get("source"):
        if column("users", "source"):
            where.append("u.source = :source")
            params["source"] = values["source"]
    # B5：source 空值筛选。true 仅未填写（NULL 或空串），false 仅已填写。
    source_is_null = values.get("source__is_null")
    if source_is_null is not None and column("users", "source"):
        if source_is_null:
            where.append("(u.source IS NULL OR u.source = '')")
        else:
            where.append("(u.source IS NOT NULL AND u.source <> '')")
    if values.get("region") in REGION_VALUES:
        if column("users", "region"):
            where.append("u.region = :region")
            params["region"] = values["region"]
    if values.get("xiangqin_status") is not None:
        # 相亲状态落在 user_privacy.match_status（1公开 2委托 3私密 4停止 5脱单）
        where.append("COALESCE(pr.match_status, 1) = :xiangqin_status")
        params["xiangqin_status"] = values["xiangqin_status"]
    if values.get("realname") is True:
        where.append("u.is_real_name = 1")
    elif values.get("realname") is False:
        where.append("COALESCE(u.is_real_name, 0) = 0")
    if values.get("cert_status") is not None:
        if column("users", "cert_status"):
            where.append("u.cert_status = :cert_status")
            params["cert_status"] = values["cert_status"]
    if values.get("wechat_added") is not None:
        if column("users", "wechat_added"):
            where.append("u.wechat_added = :wechat_added")
            params["wechat_added"] = values["wechat_added"]
    for flag in ("promoted", "newcomer", "activity_signup"):
        value = values.get(flag)
        if value is True and column("users", flag):
            where.append(f"u.{flag} = 1")
        elif value is False and column("users", flag):
            where.append(f"u.{flag} = 0")
    # B0：按具体活动筛报名。activity_signup 表无 deleted_at，以有效报名（未取消/未拒绝）为准。
    if values.get("activity_id") is not None:
        where.append(
            "EXISTS (SELECT 1 FROM activity_signup asg WHERE asg.user_id = u.id"
            " AND asg.activity_id = :activity_id AND asg.status IN (0, 1))"
        )
        params["activity_id"] = values["activity_id"]
    if values.get("offline_vip") is True:
        where.append(
            "EXISTS (SELECT 1 FROM offline_vip ov WHERE ov.user_id = u.id AND ov.deleted_at IS NULL)"
        )
    elif values.get("offline_vip") is False:
        where.append(
            "NOT EXISTS (SELECT 1 FROM offline_vip ov WHERE ov.user_id = u.id AND ov.deleted_at IS NULL)"
        )
    # 会员弃海口径（A9/B3）：以 member_abandonment 中未捞回（restored_at IS NULL）的
    # 有效记录为准。历史客源侧 status=LOST 的会员由迁移回填成 lead_sync 行，二者统一。
    abandoned = values.get("abandoned")
    if abandoned is True:
        where.append(
            "EXISTS (SELECT 1 FROM member_abandonment ma"
            " WHERE ma.user_id = u.id AND ma.restored_at IS NULL)"
        )
    elif abandoned is False:
        where.append(
            "NOT EXISTS (SELECT 1 FROM member_abandonment ma"
            " WHERE ma.user_id = u.id AND ma.restored_at IS NULL)"
        )

    # ---- 归属 / 红娘 ----
    if values.get("organization_id") is not None:
        where.append("a.organization_id = :organization_id")
        params["organization_id"] = values["organization_id"]
    if values.get("matchmaker_id") is not None:
        where.append("a.matchmaker_id = :matchmaker_id")
        params["matchmaker_id"] = values["matchmaker_id"]
    if values.get("promoter_id") is not None:
        where.append(
            "EXISTS (SELECT 1 FROM promotion_attribution pa"
            " WHERE pa.user_id = u.id AND pa.promoter_id = :promoter_id AND pa.status = 1)"
        )
        params["promoter_id"] = values["promoter_id"]

    # ---- 区间 ----
    if values.get("age_min") is not None:
        where.append("u.birthday IS NOT NULL AND TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) >= :age_min")
        params["age_min"] = values["age_min"]
    if values.get("age_max") is not None:
        where.append("u.birthday IS NOT NULL AND TIMESTAMPDIFF(YEAR, u.birthday, CURDATE()) <= :age_max")
        params["age_max"] = values["age_max"]
    if values.get("height_min") is not None:
        where.append("p.height >= :height_min")
        params["height_min"] = values["height_min"]
    if values.get("height_max") is not None:
        where.append("p.height <= :height_max")
        params["height_max"] = values["height_max"]
    # B9：体重区间，与 height_min/max 同口径；user_profile.weight 缺列时跳过而非报错。
    if values.get("weight_min") is not None and column("user_profile", "weight"):
        where.append("p.weight >= :weight_min")
        params["weight_min"] = values["weight_min"]
    if values.get("weight_max") is not None and column("user_profile", "weight"):
        where.append("p.weight <= :weight_max")
        params["weight_max"] = values["weight_max"]

    # ---- 多选维度 ----
    marriage = split_values(values.get("marriage"))
    if marriage:
        where.append(f"u.is_married IN ({_placeholders('marriage', marriage, params)})")
    education = split_values(values.get("education"))
    if education:
        where.append(f"ua.education IN ({_placeholders('education', education, params)})")
    occupation = split_values(values.get("occupation"))
    if occupation:
        where.append(f"ua.job IN ({_placeholders('occupation', occupation, params)})")
    income = split_values(values.get("income"))
    if income:
        where.append(f"p.income IN ({_placeholders('income', income, params)})")
    # 界面把收入做成区间下拉（3000以下 / 3000-8000 / 8000以上），精确匹配选不中任何值。
    if values.get("income_min") is not None:
        where.append("p.income >= :income_min")
        params["income_min"] = values["income_min"]
    if values.get("income_max") is not None:
        where.append("p.income <= :income_max")
        params["income_max"] = values["income_max"]
    # 级别 / 房子 / 民族 / 车子在界面上都标注为「多选」，取值与其它多选维度同口径
    # （英文逗号分隔 + 绑定占位符），只受理单值会让多选控件选第二个值就查不到。
    level = split_values(values.get("level"))
    if level and column("users", "level"):
        where.append(f"u.level IN ({_placeholders('level', level, params)})")
    house = split_values(values.get("house"))
    if house and column("user_profile", "house"):
        where.append(f"p.house IN ({_placeholders('house', house, params)})")
    ethnicity = split_values(values.get("ethnicity"))
    if ethnicity and column("user_profile", "ethnicity"):
        where.append(f"p.ethnicity IN ({_placeholders('ethnicity', ethnicity, params)})")
    car = split_values(values.get("car"))
    if car and column("user_profile", "car"):
        where.append(f"p.car IN ({_placeholders('car', car, params)})")

    # ---- 单值维度（与 _member_query 的 SELECT 展示列一一对应，否则「看得见筛不了」） ----
    if values.get("intention_level") is not None:
        if column("user_profile", "intention_level"):
            where.append("p.intention_level = :intention_level")
            params["intention_level"] = values["intention_level"]
    for key in ("constellation", "zodiac"):
        if values.get(key):
            if column("user_profile", key):
                where.append(f"p.{key} = :{key}")
                params[key] = values[key]

    # ---- 文本维度 ----
    for key, column_name in (("residence", "residence"), ("hometown", "hometown")):
        if values.get(key):
            where.append(f"p.{column_name} LIKE CONCAT('%', :{key}, '%')")
            params[key] = values[key]
    if values.get("hukou"):
        if column("user_profile", "household"):
            where.append("p.household LIKE CONCAT('%', :hukou, '%')")
            params["hukou"] = values["hukou"]
    if values.get("religion"):
        if column("user_profile", "religion"):
            where.append("p.religion = :religion")
            params["religion"] = values["religion"]

    # ---- 标签（JSON 数组，任一命中） ----
    tags = split_values(values.get("tags"))
    if tags:
        clauses = []
        for index, tag in enumerate(tags):
            key = f"tag_{index}"
            params[key] = f'"{tag}"'
            clauses.append(f"JSON_CONTAINS(p.tags, :{key})")
        where.append(f"({' OR '.join(clauses)})")

    # ---- 线上行为 ----
    behavior = values.get("behavior_type")
    if behavior in BEHAVIOR_TYPE_SQL:
        where.append(BEHAVIOR_TYPE_SQL[behavior])

    # ---- 时间范围 ----
    if values.get("created_start"):
        where.append("u.created_at >= :created_start")
        params["created_start"] = values["created_start"]
    if values.get("created_end"):
        # 结束日按「含当日」处理，补到次日 00:00 前。
        where.append("u.created_at < DATE_ADD(:created_end, INTERVAL 1 DAY)")
        params["created_end"] = values["created_end"]
    # 分派时间取 resource_assignment 生效记录的 effective_at，由派生表 a 提供。
    if values.get("assigned_start"):
        where.append("a.assigned_at >= :assigned_start")
        params["assigned_start"] = values["assigned_start"]
    if values.get("assigned_end"):
        where.append("a.assigned_at < DATE_ADD(:assigned_end, INTERVAL 1 DAY)")
        params["assigned_end"] = values["assigned_end"]
    # A15：预约跟进日历。按 member_follow_up 派生表的 next_follow_at 日期区间过滤，
    # 结束日按「含当日」处理，供抽屉的 全部/最近N天/指定日期 Tab 复用会员列表。
    if values.get("next_follow_start"):
        where.append("f.next_follow_at >= :next_follow_start")
        params["next_follow_start"] = values["next_follow_start"]
    if values.get("next_follow_end"):
        where.append("f.next_follow_at < DATE_ADD(:next_follow_end, INTERVAL 1 DAY)")
        params["next_follow_end"] = values["next_follow_end"]

    return " AND ".join(where), params


def member_sort_sql(
    sort_by: str | None,
    order: str | None,
    available: frozenset[tuple[str, str]] | None = None,
) -> str:
    """解析排序：order 与 sort_by 互斥，order 优先；未知值回落默认排序。

    A12-补列：`login_num` 为新增列，若传入 `available` 且其中不含
    `('users', 'login_num')`（迁移未执行），则降级为默认排序，避免拼
    `u.login_num` 报 Unknown column 拖垮整页。`available` 为 None 时不降级（兼容旧调用）。
    """
    if sort_by == "login_num" and available is not None and ("users", "login_num") not in available:
        return SORT_SQL["created_at"]
    if order in ORDER_SQL:
        return ORDER_SQL[order]
    if sort_by == "login_num":
        return "COALESCE(u.login_num, 0) DESC, u.id DESC"
    return SORT_SQL.get(str(sort_by), SORT_SQL["created_at"])
