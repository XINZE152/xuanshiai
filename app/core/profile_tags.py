"""Fixed profile tag catalog exposed to clients and used for validation."""

from __future__ import annotations

from typing import Final


TAG_CATEGORIES: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("relationship_expectation", "期望关系", ("寻找长期伴侣", "先交友看缘分", "轻松约会")),
    ("ideal_partner", "理想对象", ("希望对方喜欢运动", "希望对方有稳定工作", "希望对方幽默", "希望对方重视家庭", "希望对方有上进心", "希望对方温柔体贴", "希望对方爱旅行")),
    ("future_plan", "未来规划", ("希望1-2年内结婚", "不着急顺其自然", "丁克", "想要孩子")),
    ("love_view", "爱情观", ("期待细水长流的陪伴", "相信一见钟情", "需要个人空间", "感情需要经营", "爱情至上")),
    ("life_attitude", "生活态度", ("理性派", "理想主义", "佛系随缘", "行动派", "随性自由", "计划控")),
    ("personality", "性格特质", ("外向开朗", "内向但真诚", "有幽默感", "温柔细心", "独立自信", "善解人意")),
    ("values", "价值观", ("看重家庭", "事业优先", "追求自由", "重视精神交流", "注重健康", "乐于助人")),
    ("sports", "运动健身", ("健身", "跑步", "瑜伽", "滑雪", "徒步", "骑行", "游泳", "球类", "舞蹈", "拳击")),
    ("arts_leisure", "文艺休闲", ("电影", "阅读", "摄影", "画画", "音乐", "看展", "话剧", "写作", "书法")),
    ("education", "学历", ("高中", "大专", "本科", "硕士", "博士")),
    ("occupation", "职业", ("程序员", "教师", "医生", "律师", "设计师", "公务员", "创业者", "学生", "会计师", "护士", "工程师", "销售", "市场", "HR", "金融", "其他")),
    ("city", "城市", ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "重庆", "长沙", "郑州", "天津", "苏州", "青岛", "东莞", "沈阳", "其他")),
    ("marriage_status", "婚姻状况", ("未婚", "离异无子女", "离异有子女", "丧偶")),
    ("annual_income", "年收入", ("10万以下", "10-20万", "20-30万", "30-50万", "50万以上")),
    ("social_entertainment", "社交娱乐", ("咖啡探店", "酒吧小酌", "桌游", "剧本杀", "KTV", "宅家追剧", "密室逃脱", "逛街")),
    ("travel_outdoor", "旅行户外", ("旅行", "露营", "自驾游", "海岛度假", "登山", "城市漫步", "摄影旅行")),
    ("food_lifestyle", "美食生活", ("火锅", "下厨", "甜品", "精酿啤酒", "咖啡", "素食", "烧烤", "日料", "烘焙")),
    ("knowledge_growth", "知识成长", ("心理学", "科技数码", "财经", "历史", "哲学", "语言学习", "自我提升", "天文")),
    ("pets", "宠物", ("养猫", "养狗", "养鱼", "养植物", "喜欢宠物")),
    ("height", "身高", ("175", "180+")),
)

TAG_OPTIONS_BY_CATEGORY: Final[dict[str, frozenset[str]]] = {
    key: frozenset(options) for key, _, options in TAG_CATEGORIES
}
ALL_TAG_OPTIONS: Final[frozenset[str]] = frozenset(
    option for _, _, options in TAG_CATEGORIES for option in options
)
