"""Fixed profile tag catalog exposed to clients and used for validation."""

from __future__ import annotations

import re
from typing import Final


# 城市筛选属于基本条件，独立保留原筛选项，不暴露为兴趣标签。
DISCOVERY_CITY_OPTIONS: Final[tuple[str, ...]] = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "重庆", "长沙", "郑州", "天津", "苏州", "青岛", "东莞", "沈阳", "其他")

LEGACY_TAG_CATEGORIES: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("personality", "性格特质", (
        "外向开朗", "内向但真诚", "有幽默感", "温柔细心", "独立自信",
        "善解人意", "乐观", "自律", "有责任心", "情绪稳定",
        "懂得倾听", "好奇心旺盛", "慢热", "直接坦率", "熟人面前话多",
        "有边界感", "随和", "行动力强", "重视仪式感", "理性",
        "感性", "细节控", "随遇而安", "喜欢独处", "爱分享",
        "说到做到", "遇事不慌", "做事有计划", "爱笑", "有耐心",
        "喜欢尝试新事物", "脑洞大",
    )),
    ("sports", "运动健身", (
        "健身", "跑步", "瑜伽", "滑雪", "徒步",
        "骑行", "游泳", "球类", "舞蹈", "拳击",
        "羽毛球", "篮球", "乒乓球", "网球", "飞盘",
        "攀岩", "足球", "排球", "普拉提", "跳绳",
        "滑板", "冲浪", "潜水", "台球", "保龄球",
        "武术", "滑冰", "射箭", "马拉松", "桨板",
    )),
    ("arts_leisure", "书影音", (
        "电影", "阅读", "摄影", "画画", "音乐",
        "看展", "话剧", "写作", "书法", "播客",
        "演唱会", "小说", "科幻小说", "推理小说", "散文诗歌",
        "漫画", "动漫", "纪录片", "综艺", "音乐剧",
        "脱口秀", "Livehouse", "音乐节", "古典音乐", "摇滚",
        "民谣", "爵士", "Hip-Hop", "K-pop", "乐器演奏",
        "合唱", "胶片摄影", "手账", "陶艺", "刺绣",
        "编织", "木工", "拼贴", "水彩绘画", "逛书店",
    )),
    ("travel_outdoor", "旅行户外", (
        "旅行", "露营", "自驾游", "海岛度假", "登山",
        "城市漫步", "摄影旅行", "周末短途", "自由行", "背包旅行",
        "房车旅行", "火车旅行", "看日出", "追日落", "海边散步",
        "公园野餐", "古镇漫游", "博物馆打卡", "星空露营", "逛当地菜市场",
        "去小城住几天", "做旅行攻略",
    )),
    ("food_lifestyle", "美食生活", (
        "火锅", "下厨", "甜品", "精酿啤酒", "咖啡",
        "素食", "烧烤", "日料", "烘焙", "咖啡探店",
        "手冲咖啡", "喝茶", "面食", "粤菜", "川湘菜",
        "东南亚菜", "西餐", "地方小吃", "夜市寻味", "早餐爱好者",
        "研究家常菜", "周末煲汤", "一人食", "做便当", "低糖烘焙",
        "收纳整理", "冰淇淋", "酸辣口", "重口味", "清淡口",
    )),
    ("games", "游戏娱乐", (
        "桌游", "剧本杀", "KTV", "宅家追剧", "密室逃脱",
        "休闲手游", "主机游戏", "PC游戏", "拼图", "乐高",
        "魔方", "飞镖", "街机", "音游", "解谜游戏",
        "策略游戏", "模拟经营", "合作闯关", "独立游戏", "围棋",
        "象棋", "纸牌游戏",
    )),
    ("pets", "宠物园艺", (
        "养猫", "养狗", "养鱼", "养植物", "喜欢宠物",
        "园艺", "云吸猫", "云吸狗", "鸟类观察", "水族造景",
        "多肉植物", "阳台种菜", "花艺", "认植物", "逛花市",
        "养兔", "小动物摄影", "自然笔记",
    )),
    ("knowledge_growth", "知识成长", (
        "心理学", "科技数码", "财经", "历史", "哲学",
        "语言学习", "自我提升", "天文", "学点编程", "AI工具",
        "科普阅读", "数学解谜", "经济学", "人文地理", "建筑欣赏",
        "练习表达", "时间管理", "记账复盘", "学习新技能", "读书笔记",
        "逛科技馆", "参加读书会",
    )),
)

# 兴趣标签是面向用户的统一名称；personality 仍用于兼容旧存储列。
# 目录按浏览场景拆细，每个标签只出现在一个当前分类中。
TAG_CATEGORIES: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("personality", "性格特质", (
        "外向开朗", "内向但真诚", "有幽默感", "温柔细心", "独立自信", "善解人意", "情绪稳定",
        "懂得倾听", "慢热", "直接坦率", "熟人面前话多", "有边界感", "随和", "行动力强",
        "重视仪式感", "细节控", "喜欢独处", "爱分享", "说到做到", "遇事不慌", "做事有计划",
        "爱笑", "有耐心", "喜欢尝试新事物", "脑洞大", "遇到问题先沟通", "需要独处充电", "熟悉后很健谈",
    )),
    ("sports", "运动", (
        "力量训练", "跑步", "瑜伽", "滑雪", "周末徒步", "骑行", "游泳", "打羽毛球", "打篮球",
        "打乒乓球", "打网球", "玩飞盘", "室内攀岩", "踢足球", "打排球", "练普拉提", "跳绳",
        "玩滑板", "冲浪", "潜水", "打台球", "打保龄球", "练武术", "滑冰", "射箭", "跑马拉松",
        "玩桨板", "跳街舞",
    )),
    ("reading", "阅读", (
        "科幻小说", "推理小说", "散文诗歌", "人文社科", "人物传记", "纸质书", "电子书", "泡图书馆",
        "逛独立书店", "睡前读几页", "做读书笔记", "参加读书会", "看历史故事", "读心理学",
        "看自然科普", "读商业传记", "收集旧书", "分享书单",
    )),
    ("film_tv", "影视综", (
        "纪录片", "脱口秀", "宅家追剧", "悬疑片", "科幻片", "喜剧片", "动画电影", "老电影",
        "逛电影节", "看院线首映", "追国产剧", "追英美剧", "看日剧", "看韩剧", "刷经典港片",
        "看真人秀", "看美食纪录片", "写观后感",
    )),
    ("music", "音乐", (
        "民谣", "摇滚", "爵士", "Hip-Hop", "K-pop", "古典乐", "独立音乐", "电子乐", "R&B",
        "听黑胶", "弹吉他", "弹钢琴", "玩乐队", "练声乐", "参加合唱", "去看Livehouse",
        "去音乐节", "看演唱会", "通勤听歌", "分享歌单",
    )),
    ("arts", "文艺创作", (
        "拍胶片", "街头摄影", "画水彩", "画插画", "逛美术馆", "逛摄影展", "看话剧", "看音乐剧",
        "练书法", "做手账", "捏陶艺", "做刺绣", "学编织", "做木工", "玩拼贴", "写随笔",
        "做独立刊物", "逛创意市集",
    )),
    ("anime", "二次元", (
        "追番", "看国漫", "看日漫", "收集手办", "拼模型", "玩Cosplay", "做同人创作", "逛漫展",
        "关注声优", "看热血番", "看治愈番", "看推理番", "补经典动画", "画二次元插画",
    )),
    ("travel_outdoor", "旅行户外", (
        "露营", "自驾游", "海岛度假", "登山", "城市漫步", "摄影旅行", "周末短途", "自由行", "背包旅行",
        "房车旅行", "坐火车慢游", "看日出", "追日落", "海边散步", "公园野餐", "古镇漫游",
        "逛当地菜市场", "去小城住几天", "做旅行攻略", "探访冷门目的地", "住青旅认识朋友", "带父母出游",
    )),
    ("food", "美食", (
        "火锅", "甜品", "烧烤", "日料", "面食", "粤菜", "川湘菜", "东南亚菜", "西餐", "地方小吃",
        "夜市寻味", "早餐爱好者", "研究家常菜", "周末煲汤", "一人食", "做便当", "低糖烘焙",
        "冰淇淋", "酸辣口", "重口味", "清淡口", "会做几道拿手菜", "复刻餐厅菜", "寻找街坊小店",
    )),
    ("drinks", "咖啡茶酒", (
        "精酿啤酒", "手冲咖啡", "喝茶", "葡萄酒", "鸡尾酒", "威士忌", "清酒", "无酒精特调",
        "逛小酒馆", "探咖啡店", "自己磨咖啡豆", "收集茶具", "喝单丛", "研究风味轮", "周末泡茶",
    )),
    ("games", "游戏", (
        "桌游", "剧本杀", "密室逃脱", "休闲手游", "主机游戏", "PC游戏", "拼图", "乐高", "魔方",
        "街机", "音游", "解谜游戏", "策略游戏", "模拟经营", "合作闯关", "独立游戏", "围棋",
        "象棋", "纸牌游戏", "和朋友联机",
    )),
    ("leisure", "休闲娱乐", (
        "KTV", "飞镖", "听播客", "逛市集", "泡温泉", "逛公园", "周末探店", "朋友小聚",
        "在家放空", "做拼豆", "玩桌上足球", "逛二手店", "去游乐园", "看开放麦", "泡澡听歌", "收集冰箱贴",
    )),
    ("pets", "宠物", (
        "养猫", "养狗", "养鱼", "云吸猫", "云吸狗", "养兔", "小动物摄影", "给宠物做饭",
        "带狗散步", "研究猫咪行为", "逛宠物友好店", "做流浪动物志愿者", "养仓鼠", "养鹦鹉",
    )),
    ("gardening", "植物园艺", (
        "养绿植", "水族造景", "多肉植物", "阳台种菜", "做花艺", "认植物", "逛花市", "写自然笔记",
        "给植物换盆", "研究土壤配方", "种香草", "插花", "去公园观鸟", "收集种子",
    )),
    ("cars", "汽车文化", (
        "看车评", "新能源车", "经典车", "赛车运动", "摩托骑行", "汽车摄影", "研究自驾路线", "逛车展",
        "模拟赛车", "动手洗车", "研究汽车设计", "看F1", "改装文化", "周末山路自驾",
    )),
    ("lifestyle", "生活习惯", (
        "收纳整理", "早起派", "晚睡型", "规律作息", "居家派", "极简生活", "断舍离", "爱做家务",
        "周末宅家", "记生活日常", "带饭上班", "每周固定运动", "喜欢下厨", "睡前不看手机",
        "每月做计划", "保持房间整洁", "喜欢慢节奏", "通勤骑车",
    )),
    ("knowledge_growth", "知识成长", (
        "科技数码", "财经", "历史", "哲学", "语言学习", "天文", "学点编程", "AI工具", "数学解谜",
        "经济学", "人文地理", "建筑欣赏", "练习表达", "时间管理", "记账复盘", "学习新技能",
        "逛科技馆", "上公开课", "听知识播客", "学一门外语", "研究城市历史", "做知识卡片",
    )),
)
TAG_OPTIONS_BY_CATEGORY: Final[dict[str, frozenset[str]]] = {
    key: frozenset(options) for key, _, options in TAG_CATEGORIES
}
LEGACY_TAG_OPTIONS_BY_CATEGORY: Final[dict[str, frozenset[str]]] = {
    key: frozenset(options) for key, _, options in LEGACY_TAG_CATEGORIES
}
ALL_TAG_OPTIONS: Final[frozenset[str]] = frozenset(
    option for _, _, options in TAG_CATEGORIES for option in options
)
_TAG_OPTION_BY_CASEFOLD: Final[dict[str, str]] = {
    option.casefold(): option for option in ALL_TAG_OPTIONS
}
PERSONALITY_OPTIONS: Final[frozenset[str]] = TAG_OPTIONS_BY_CATEGORY["personality"]
MAX_PERSONAL_TAGS: Final[int] = 10
MAX_CUSTOM_TAGS: Final[int] = 3
CUSTOM_TAG_MIN_LENGTH: Final[int] = 2
CUSTOM_TAG_MAX_LENGTH: Final[int] = 10
TAG_CATALOG_REVISION: Final[str] = "2026-09-12.1"
CUSTOM_TAG_CATEGORY_KEY: Final[str] = "custom"
CUSTOM_TAG_CATEGORY_MAP_KEY: Final[str] = "custom_categories"
_CUSTOM_TAG_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9·+&\- ]+$")
_CUSTOM_TAG_FORBIDDEN_TERMS: Final[tuple[str, ...]] = (
    "找对象", "找伴侣", "寻找伴侣", "长期伴侣", "另一半", "恋爱", "结婚", "婚姻", "生育",
    "身高", "体重", "年龄", "学历", "本科", "硕士", "博士", "收入", "职业", "房产", "资产",
    "微信", "电话", "手机", "qq", "vx", "v信", "wx", "wechat", "加我", "约炮", "包养", "未成年",
    "政治", "宗教", "疾病",
)


def normalize_custom_tag(value: str) -> str:
    """Normalize and validate one user-authored interest label."""
    normalized = " ".join(str(value).strip().split())
    if not CUSTOM_TAG_MIN_LENGTH <= len(normalized) <= CUSTOM_TAG_MAX_LENGTH:
        raise ValueError(f"自定义标签需为{CUSTOM_TAG_MIN_LENGTH}到{CUSTOM_TAG_MAX_LENGTH}个字符")
    if not _CUSTOM_TAG_PATTERN.fullmatch(normalized) or normalized.isdigit():
        raise ValueError("自定义标签只能使用中英文、数字和常用连接符，且不能为纯数字")
    if sum(character.isdigit() for character in normalized) >= 6:
        raise ValueError("自定义标签不能包含联系方式")
    lowered = normalized.casefold()
    if any(term.casefold() in lowered for term in _CUSTOM_TAG_FORBIDDEN_TERMS):
        raise ValueError("自定义标签只能描述本人的兴趣、生活偏好或性格")
    return normalized


def custom_tags(values: list[str]) -> list[str]:
    """Return unique, locally valid custom labels from their explicit storage group."""
    result: list[str] = []
    for value in values:
        try:
            normalized = normalize_custom_tag(value)
        except ValueError:
            continue
        if normalized.casefold() not in _TAG_OPTION_BY_CASEFOLD and normalized.casefold() not in {item.casefold() for item in result}:
            result.append(normalized)
    return result[:MAX_CUSTOM_TAGS]


def normalize_custom_tag_categories(
    values: list[str], categories: object
) -> dict[str, str]:
    """Keep category assignments for valid custom labels in the current catalog."""
    selected = custom_tags(values)
    if not isinstance(categories, dict):
        return {}
    normalized_input: dict[str, str] = {}
    for raw_label, raw_category in categories.items():
        try:
            label = normalize_custom_tag(str(raw_label))
        except ValueError:
            continue
        category = str(raw_category)
        if category in TAG_OPTIONS_BY_CATEGORY and label not in ALL_TAG_OPTIONS:
            normalized_input[label.casefold()] = category
    return {
        label: normalized_input[label.casefold()]
        for label in selected
        if label.casefold() in normalized_input
    }


def validate_personal_tag_selection(values: list[str]) -> list[str]:
    """Validate a mixed system/custom selection and return normalized labels."""
    result: list[str] = []
    seen: set[str] = set()
    custom_count = 0
    for value in values:
        raw = " ".join(str(value).strip().split())
        normalized = _TAG_OPTION_BY_CASEFOLD.get(raw.casefold())
        if normalized is None:
            normalized = normalize_custom_tag(raw)
        identity = normalized.casefold()
        if identity in seen:
            raise ValueError("标签不能重复")
        if normalized not in ALL_TAG_OPTIONS:
            custom_count += 1
        result.append(normalized)
        seen.add(identity)
    if custom_count > MAX_CUSTOM_TAGS:
        raise ValueError(f"自定义标签最多添加{MAX_CUSTOM_TAGS}个")
    return result


def personal_tags(values: list[str], stored_custom_tags: list[str] | None = None) -> list[str]:
    """Read legacy values without changing storage or inferring partner preferences."""
    allowed_custom = set(custom_tags(stored_custom_tags or []))
    return list(dict.fromkeys(tag for tag in values if tag in ALL_TAG_OPTIONS or tag in allowed_custom))


def split_personal_tags(values: list[str], stored_custom_tags: list[str] | None = None) -> tuple[list[str], list[str]]:
    tags = personal_tags(values, stored_custom_tags)
    return ([tag for tag in tags if tag not in PERSONALITY_OPTIONS],
            [tag for tag in tags if tag in PERSONALITY_OPTIONS])
