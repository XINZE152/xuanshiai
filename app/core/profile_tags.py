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
    ("personality", "性格特质", LEGACY_TAG_CATEGORIES[0][2]),
    ("sports", "运动", LEGACY_TAG_CATEGORIES[1][2]),
    ("reading", "阅读", (
        "阅读", "小说", "科幻小说", "推理小说", "散文诗歌",
        "写作", "逛书店", "人文社科", "人物传记", "纸质书",
        "电子书", "泡图书馆",
    )),
    ("film_tv", "影视综", (
        "电影", "纪录片", "综艺", "脱口秀", "宅家追剧",
        "悬疑片", "科幻片", "喜剧片", "动画电影", "老电影", "电影节",
    )),
    ("music", "音乐", (
        "音乐", "演唱会", "Livehouse", "音乐节", "古典音乐",
        "摇滚", "民谣", "爵士", "Hip-Hop", "K-pop",
        "乐器演奏", "合唱", "黑胶唱片",
    )),
    ("arts", "文艺创作", (
        "摄影", "画画", "看展", "话剧", "音乐剧", "书法",
        "胶片摄影", "手账", "陶艺", "刺绣", "编织",
        "木工", "拼贴", "水彩绘画",
    )),
    ("anime", "二次元", (
        "漫画", "动漫", "国漫", "日漫", "手办模型",
        "Cosplay", "同人创作", "逛漫展", "声优作品",
    )),
    ("travel_outdoor", "旅行户外", LEGACY_TAG_CATEGORIES[3][2]),
    ("food", "美食", (
        "火锅", "下厨", "甜品", "素食", "烧烤",
        "日料", "烘焙", "咖啡探店", "面食", "粤菜",
        "川湘菜", "东南亚菜", "西餐", "地方小吃", "夜市寻味",
        "早餐爱好者", "研究家常菜", "周末煲汤", "一人食", "做便当",
        "低糖烘焙", "冰淇淋", "酸辣口", "重口味", "清淡口",
    )),
    ("drinks", "咖啡茶酒", (
        "精酿啤酒", "咖啡", "手冲咖啡", "喝茶", "葡萄酒",
        "鸡尾酒", "威士忌", "清酒", "无酒精特调", "逛小酒馆",
    )),
    ("games", "游戏", (
        "桌游", "剧本杀", "密室逃脱", "休闲手游", "主机游戏",
        "PC游戏", "拼图", "乐高", "魔方", "街机",
        "音游", "解谜游戏", "策略游戏", "模拟经营", "合作闯关",
        "独立游戏", "围棋", "象棋", "纸牌游戏",
    )),
    ("leisure", "休闲娱乐", (
        "KTV", "飞镖", "播客", "逛市集", "泡温泉",
        "逛公园", "周末探店", "看现场演出", "朋友小聚", "在家放空",
    )),
    ("pets", "宠物", (
        "养猫", "养狗", "养鱼", "喜欢宠物", "云吸猫",
        "云吸狗", "鸟类观察", "养兔", "小动物摄影",
    )),
    ("gardening", "植物园艺", (
        "养植物", "园艺", "水族造景", "多肉植物", "阳台种菜",
        "花艺", "认植物", "逛花市", "自然笔记",
    )),
    ("cars", "汽车文化", (
        "汽车文化", "看车评", "新能源车", "经典车", "赛车运动",
        "摩托骑行", "汽车摄影", "研究自驾路线", "逛车展",
    )),
    ("lifestyle", "生活习惯", (
        "收纳整理", "早起派", "晚睡型", "规律作息", "居家派",
        "极简生活", "断舍离", "爱做家务", "周末宅家", "记生活日常",
        "带饭上班", "定期运动",
    )),
    ("knowledge_growth", "知识成长", LEGACY_TAG_CATEGORIES[7][2]),
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
TAG_CATALOG_REVISION: Final[str] = "2026-09-10.4"
CUSTOM_TAG_CATEGORY_KEY: Final[str] = "custom"
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


def validate_personal_tag_selection(values: list[str]) -> list[str]:
    """Validate a mixed system/custom selection and return normalized labels."""
    result: list[str] = []
    seen: set[str] = set()
    custom_count = 0
    for value in values:
        normalized = value if value in ALL_TAG_OPTIONS else normalize_custom_tag(value)
        normalized = _TAG_OPTION_BY_CASEFOLD.get(normalized.casefold(), normalized)
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
