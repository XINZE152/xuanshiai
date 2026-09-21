"""填充本地开发库的完整会员数据，使 C 端功能（除红娘）全部可验证。

与 ``seed_community_demo.py`` 的分工：
    - ``seed_community_demo.py`` 负责社区内容演示行（话题/动态/活动/纸飞机/Banner）。
    - 本脚本负责"会员本身"：16 位演示会员的完整资料、认证材料、会员权益、
      积分账本、关系链、聊天、通知，以及发现页可见性所需的全部前置条件。

设计要点：
    - 只在 development/testing 执行，其它环境直接拒绝。
    - 幂等：所有写入以手机号 / user_id / 自然键做 UPSERT；日志型表按业务键判重后追加。
    - 生成的素材统一写入 ``storage/uploads/<user_id>/demo-*``，可安全重复执行。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_STATIC = ROOT.parent / "xuanshiai-vue" / "static"
DEMO_PASSWORD = "password123"
DEMO_SLUG = "demo"

# 已存在的演示会员（补全，不新建）。
EXISTING_PHONES = (
    "13800001001",
    "13800001002",
    "13998020600",
    "13905000870",
    "17870810285",
    "17870810286",
    "17870810291",
)

# C 端主测试账号（账号一）。补齐后 C 端门禁才全部打开。
PRIMARY_VIEWER_PHONE = "19730552884"

_EDUCATION_DEGREE = {1: "高中", 2: "大专", 3: "本科", 4: "硕士", 5: "博士"}

_MBTI_POLES = (("E", "I"), ("S", "N"), ("T", "F"), ("J", "P"))

_CONSTELLATIONS = (
    ((1, 20), "水瓶座"), ((2, 19), "双鱼座"), ((3, 21), "白羊座"), ((4, 20), "金牛座"),
    ((5, 21), "双子座"), ((6, 22), "巨蟹座"), ((7, 23), "狮子座"), ((8, 23), "处女座"),
    ((9, 23), "天秤座"), ((10, 24), "天蝎座"), ((11, 23), "射手座"), ((12, 22), "摩羯座"),
)

_ZODIAC_NAMES = ("鼠", "牛", "虎", "兔", "龙", "蛇", "马", "羊", "猴", "鸡", "狗", "猪")
_ZODIAC_ANCHOR_YEAR = 1900  # 1900 年为鼠年

_EXTRA_CERT_TYPES = (
    ("收入认证", "上传近半年银行流水或完税证明，仅用于人工核对，不对其他会员展示。"),
    ("驾驶证认证", "上传本人有效驾驶证，用于确认出行方式与同城活动范围。"),
    ("宠物免疫认证", "上传宠物免疫证明，便于匹配同样养宠的会员。"),
)

# 与 app/services/payments.py 的 BOOST_PACKAGES 保持一致；
# 表本身由建表脚本创建但不带数据，这里补齐以便置顶曝光链路可验证。
_BOOST_PACKAGES = (
    (1, "爆灯", 0, "5.00", 10),
    (2, "置顶1天", 1, "1.00", 20),
    (2, "置顶7天", 7, "5.00", 21),
    (2, "置顶30天", 30, "15.00", 22),
)


def _member(**kwargs: Any) -> dict[str, Any]:
    """Declare one demo member; missing optional keys get safe defaults."""
    defaults: dict[str, Any] = {
        "membership": None,
        "boost_active": False,
        "intent": "self_match",
        "custom_tag": None,
        "custom_tag_category": None,
        "is_married": 1,
        "accept_long_distance": 0,
        "house": "暂无房",
        "car": "暂无车",
        "smoking": "不吸烟",
        "single_reason": "工作节奏较快，希望认真开始一段关系。",
        "family_background": "家庭氛围平和，父母尊重我的选择。",
        "children_intention": "顺其自然",
        "preferred_occupation": "不限",
        "extra_requirement": "希望双方能坦诚沟通，对长期关系有共识。",
        "education_min": 3,
        "income_min": 8000,
        "preferred_province_code": None,
        "preferred_city_codes": (),
    }
    defaults.update(kwargs)
    city_code = str(defaults["city_code"])
    hometown_code = str(defaults["hometown_code"])
    defaults["province_code"] = f"{city_code[:2]}0000"
    defaults["hometown_province_code"] = f"{hometown_code[:2]}0000"
    defaults["birthday_date"] = date.fromisoformat(defaults["birthday"])
    defaults["age"] = _age_of(defaults["birthday_date"])
    return defaults


def _age_of(birthday: date) -> int:
    today = date.today()
    return today.year - birthday.year - ((today.month, today.day) < (birthday.month, birthday.day))


def _constellation(birthday: date) -> str:
    """Return the western zodiac sign for one birthday."""
    for (month, day), name in _CONSTELLATIONS:
        if (birthday.month, birthday.day) < (month, day):
            index = _CONSTELLATIONS.index(((month, day), name))
            return _CONSTELLATIONS[index - 1][1]
    return "摩羯座"


def _zodiac(birthday: date) -> str:
    """Return the Chinese zodiac animal for one birthday."""
    return _ZODIAC_NAMES[(birthday.year - _ZODIAC_ANCHOR_YEAR) % 12]


MEMBERS: tuple[dict[str, Any], ...] = (
    _member(
        phone="13800001001", nickname="林知夏", real_name="林知夏", gender=2,
        birthday="1996-05-18", avatar="anime:001", id_card="320105199605180021",
        city="南京", city_code="320100", district_code="320105",
        hometown="南京", hometown_code="320100",
        occupation="内容策划", industry="文化传媒", company="南京一间文化传播有限公司",
        education_level=3, school="南京大学",
        height=165, weight=52, income=18000, mbti="INFJ",
        intro="做内容策划第六年，喜欢把复杂的事情慢慢说清楚。周末常去先锋书店待一下午，也愿意为一场好展览跨城。",
        love_view="先认真认识，再一起决定关系的节奏；坦诚不等于急着给答案。",
        ideal_partner="希望对方情绪稳定、愿意沟通，能接受我偶尔需要独处充电。",
        hobbies="阅读、散步、城市旅行、手账",
        interest_tags=("逛独立书店", "做读书笔记", "城市漫步", "逛美术馆"),
        personality_tags=("懂得倾听", "有边界感", "情绪稳定", "慢热"),
        photos=("anime:010", "portrait:profile-woman-community.webp"),
        single_reason="工作节奏快，宁缺毋滥。",
        family_background="父母在南京，家庭氛围松弛，支持我自己的节奏。",
        house="已购房",
        dating_goal="倾向结婚", meeting_pace="先线上熟悉，再约线下",
        children_intention="愿意要孩子",
        extra_requirement="希望对方在南京或愿意来南京长期发展。",
        age_range=(27, 36), height_range=(170, 190), income_min=12000,
        preferred_province_code="320000", preferred_city_codes=("320100",),
        membership="yearly", boost_active=True,
        custom_tag="周末看展搭子", custom_tag_category="arts",
    ),
    _member(
        phone="13800001002", nickname="周予安", real_name="周予安", gender=1,
        birthday="1995-09-12", avatar="anime:003", id_card="310104199509120013",
        city="上海", city_code="310100", district_code="310104",
        hometown="扬州", hometown_code="321000",
        occupation="建筑设计", industry="设计服务", company="上海拾光建筑设计事务所",
        education_level=4, school="东南大学",
        height=178, weight=70, income=26000, mbti="ISFP",
        intro="建筑设计从业七年，做过老城改造，也在慢慢学怎么把生活过细。工作之外喜欢拍照，愿意认真听别人讲完一件事。",
        love_view="舒服的关系应该让双方都能做自己，而不是互相改造。",
        ideal_partner="希望对方有自己的热爱，也愿意一起把普通日子过得有意思。",
        hobbies="摄影、音乐、城市漫步、骑行",
        interest_tags=("街头摄影", "城市漫步", "独立音乐", "跑马拉松"),
        personality_tags=("温柔细心", "做事有计划", "说到做到", "随和"),
        photos=("anime:020", "portrait:profile-man-light.webp"),
        single_reason="之前忙于项目，现在想把生活重心挪回来。",
        family_background="普通工薪家庭，父母在扬州，关系亲近但不干涉。",
        house="已购房", car="已购车",
        dating_goal="倾向恋爱", meeting_pace="可以先约一场展览或散步",
        extra_requirement="希望双方都能尊重彼此的工作节奏。",
        age_range=(25, 34), height_range=(155, 172), income_min=10000,
        preferred_province_code="310000", preferred_city_codes=("310100",),
        accept_long_distance=1,
        membership="quarterly",
    ),
    _member(
        phone="13998020600", nickname="顾言澄", real_name="顾言澄", gender=2,
        birthday="1997-02-26", avatar="anime:004", id_card="320104199702260028",
        city="南京", city_code="320100", district_code="320104",
        hometown="苏州", hometown_code="320500",
        occupation="心理咨询助理", industry="教育服务", company="南京心桥心理咨询中心",
        education_level=3, school="南京师范大学",
        height=162, weight=50, income=13000, mbti="ENFP",
        intro="在心理咨询中心做助理，每天最常提醒自己的一句话是：先照顾好自己。相信表达和倾听都需要练习，周末常去看展或做点心。",
        love_view="坦诚说出需要，也尊重对方的边界。",
        ideal_partner="希望对方愿意沟通、不回避冲突，能一起把问题说开。",
        hobbies="看展、烘焙、读书、瑜伽",
        interest_tags=("逛摄影展", "低糖烘焙", "读心理学", "瑜伽"),
        personality_tags=("外向开朗", "善解人意", "爱分享", "有耐心"),
        photos=("anime:012", "portrait:profile-woman-alt.webp"),
        single_reason="上一段关系结束后，花了一段时间重新认识自己。",
        family_background="父母都在苏州做老师，家里很重视沟通。",
        house="与家人同住",
        dating_goal="倾向恋爱", meeting_pace="一周内可以线下见面",
        children_intention="愿意要孩子",
        extra_requirement="希望对方有自己的朋友圈子和生活重心。",
        age_range=(26, 35), height_range=(170, 185), income_min=10000,
        preferred_province_code="320000", preferred_city_codes=("320100", "320500"),
        membership="monthly",
        custom_tag="手冲咖啡品鉴", custom_tag_category="drinks",
    ),
    _member(
        phone="13905000870", nickname="沈知意", real_name="沈知意", gender=2,
        birthday="1998-11-03", avatar="anime:005", id_card="310101199811030024",
        city="上海", city_code="310100", district_code="310101",
        hometown="无锡", hometown_code="320200",
        occupation="产品经理", industry="互联网", company="上海云枢科技有限公司",
        education_level=3, school="上海大学",
        height=168, weight=54, income=24000, mbti="INTP",
        intro="互联网产品经理，习惯把需求拆成小步骤再一个个解决。喜欢把生活过得有条理，也保留一点随性，下班后常去游泳或看一场老电影。",
        love_view="好的关系需要明确，也需要留有空间。",
        ideal_partner="希望对方独立、讲道理，能接受我把事情先想清楚再回应。",
        hobbies="游泳、电影、羽毛球、手账",
        interest_tags=("科幻片", "游泳", "打羽毛球", "做手账"),
        personality_tags=("独立自信", "直接坦率", "情绪稳定", "喜欢独处"),
        photos=("anime:014", "portrait:profile-woman-main.webp"),
        single_reason="工作强度大，也在学习把时间留给自己和关系。",
        family_background="父母在无锡经营小生意，从小比较独立。",
        house="已购房",
        dating_goal="倾向结婚", meeting_pace="先聊清楚彼此的节奏再见面",
        preferred_occupation="互联网/金融",
        extra_requirement="希望对方收入和工作稳定，对长期生活有规划。",
        age_range=(27, 36), height_range=(172, 188), education_min=4, income_min=15000,
        preferred_province_code="310000", preferred_city_codes=("310100",),
        membership="yearly", boost_active=True,
    ),
    _member(
        phone="17870810285", nickname="许闻洲", real_name="许闻洲", gender=1,
        birthday="1994-07-21", avatar="anime:006", id_card="330106199407210015",
        city="杭州", city_code="330100", district_code="330106",
        hometown="合肥", hometown_code="340100",
        occupation="高校教师", industry="教育", company="杭州某高校",
        education_level=5, school="浙江大学",
        height=180, weight=74, income=21000, mbti="ISTJ",
        intro="在高校教书，带两门专业课。生活节奏稳定，喜欢骑行和做一顿慢饭，也保持每周读一本历史书的习惯。",
        love_view="长期关系靠日常的尊重和兑现承诺。",
        ideal_partner="希望对方作息规律、有自己的事做，能一起把日子过稳。",
        hobbies="骑行、做饭、历史书、露营",
        interest_tags=("骑行", "研究家常菜", "看历史故事", "露营"),
        personality_tags=("做事有计划", "说到做到", "随和", "有耐心"),
        photos=("anime:022", "portrait:profile-man-alt.webp"),
        single_reason="博士毕业后先站稳工作，感情的事排在了后面。",
        family_background="父母在合肥，家庭氛围传统但开明。",
        house="已购房", car="已购车",
        dating_goal="倾向结婚", meeting_pace="先线上了解，节奏可以慢一点",
        children_intention="愿意要孩子", preferred_occupation="教育/科研/医疗",
        extra_requirement="希望对方在杭州长期发展，双方家庭能互相走动。",
        age_range=(26, 35), height_range=(158, 172), education_min=4, income_min=10000,
        preferred_province_code="330000", preferred_city_codes=("330100",),
    ),
    _member(
        phone="17870810286", nickname="唐婉", real_name="唐婉", gender=2,
        birthday="1996-08-09", avatar="anime:007", id_card="330105199608090026",
        city="杭州", city_code="330100", district_code="330105",
        hometown="杭州", hometown_code="330100",
        occupation="品牌运营", industry="消费品", company="杭州拾味食品有限公司",
        education_level=3, school="浙江工商大学",
        height=163, weight=50, income=16000, mbti="ESFJ",
        intro="做消费品品牌运营，习惯把生活里的小事记录下来。愿意分享，也在学习照顾自己，周末会去上瑜伽课或找一家新的咖啡店。",
        love_view="互相支持，比漂亮的承诺更重要。",
        ideal_partner="希望对方顾家、情绪稳定，愿意一起安排周末和假期。",
        hobbies="瑜伽、咖啡、旅行、逛市集",
        interest_tags=("瑜伽", "探咖啡店", "周末探店", "去音乐节"),
        personality_tags=("外向开朗", "爱分享", "重视仪式感", "爱笑"),
        photos=("anime:016", "portrait:profile-woman-community.webp"),
        single_reason="这两年把精力放在工作和家人身上，现在想认真开始一段关系。",
        family_background="土生土长杭州人，父母在身边，家庭氛围热闹。",
        house="与家人同住", car="已购车",
        dating_goal="倾向结婚", meeting_pace="一周内可以线下见面",
        children_intention="愿意要孩子",
        extra_requirement="希望对方在杭州有稳定工作，双方能常见面。",
        age_range=(27, 36), height_range=(172, 186), income_min=12000,
        preferred_province_code="330000", preferred_city_codes=("330100",),
        membership="quarterly",
    ),
    _member(
        phone="17870810291", nickname="程野", real_name="程野", gender=1,
        birthday="1997-12-15", avatar="anime:009", id_card="320102199712150011",
        city="南京", city_code="320100", district_code="320102",
        hometown="南通", hometown_code="320600",
        occupation="公益项目专员", industry="社会服务", company="南京同路人公益发展中心",
        education_level=3, school="河海大学",
        height=176, weight=70, income=12000, mbti="ENFJ",
        intro="在公益机构做社区项目，日常是跑社区、写方案、陪志愿者一起干活。周末常参加公益活动，想认识同样愿意把日子过好的人。",
        love_view="彼此诚实、一起成长，关系才有安全感。",
        ideal_partner="希望对方善良、有行动力，能理解我的工作节奏。",
        hobbies="徒步、公益、吉他、篮球",
        interest_tags=("周末徒步", "打篮球", "弹吉他", "公园野餐"),
        personality_tags=("行动力强", "爱分享", "随和", "遇事不慌"),
        photos=("anime:024", "portrait:profile-man-light.webp"),
        single_reason="之前一直在外地做项目，刚回南京稳定下来。",
        family_background="父母在南通，普通家庭，很支持我做公益。",
        dating_goal="倾向恋爱", meeting_pace="可以先约一场徒步或公益活动",
        children_intention="愿意要孩子",
        extra_requirement="希望对方不介意我的收入水平，看重生活状态。",
        age_range=(24, 33), height_range=(155, 172), education_min=2, income_min=6000,
        preferred_province_code="320000", preferred_city_codes=("320100", "320600"),
    ),
    # ---------------------------- 新增会员 ----------------------------
    _member(
        phone="13800001011", nickname="苏芷宁", real_name="苏芷宁", gender=2,
        birthday="1997-03-14", avatar="anime:011", id_card="320102199703140023",
        city="南京", city_code="320100", district_code="320102",
        hometown="扬州", hometown_code="321000",
        occupation="财务分析师", industry="金融", company="南京汇诚资本管理有限公司",
        education_level=3, school="南京审计大学",
        height=166, weight=51, income=20000, mbti="ISFJ",
        intro="在资产管理公司做财务分析，工作里习惯把数据核对清楚，生活里也喜欢把日子安排得有条理，周末会去公园跑步或在家研究新的烘焙配方。",
        love_view="关系里最重要的是稳定和可预期，说到的事情尽量做到。",
        ideal_partner="希望对方踏实、有储蓄和长期规划，愿意一起讨论现实问题。",
        hobbies="跑步、烘焙、理财、看纪录片",
        interest_tags=("跑马拉松", "低糖烘焙", "看美食纪录片", "记账复盘"),
        personality_tags=("做事有计划", "细节控", "情绪稳定", "慢热"),
        photos=("anime:017", "portrait:profile-woman-alt.webp"),
        single_reason="工作和考证占满了前几年，现在想把生活补回来。",
        family_background="父母在扬州，家里观念传统，希望我尽早稳定。",
        house="已购房",
        dating_goal="倾向结婚", meeting_pace="先线上熟悉，再约线下",
        children_intention="愿意要孩子", preferred_occupation="金融/财务/审计",
        extra_requirement="希望对方有稳定收入，双方对婚后财务安排能提前聊清楚。",
        age_range=(27, 36), height_range=(172, 188), income_min=13000,
        preferred_province_code="320000", preferred_city_codes=("320100",),
        membership="yearly", boost_active=True,
    ),
    _member(
        phone="13800001012", nickname="陆云舟", real_name="陆云舟", gender=1,
        birthday="1995-06-08", avatar="anime:013", id_card="320105199506080017",
        city="南京", city_code="320100", district_code="320105",
        hometown="常州", hometown_code="320400",
        occupation="结构工程师", industry="建筑工程", company="南京城建设计研究院",
        education_level=4, school="南京航空航天大学",
        height=182, weight=76, income=23000, mbti="INTJ",
        intro="结构工程师，平时和图纸、模型打交道，习惯把问题拆到最小单位再解决。喜欢一个人听音乐，也愿意为朋友做一顿认真的饭。",
        love_view="我希望关系是两个独立的人互相加分，而不是彼此消耗。",
        ideal_partner="希望对方有自己的判断和节奏，我们能就事论事地讨论分歧。",
        hobbies="音乐、做饭、游泳、看展",
        interest_tags=("独立音乐", "研究家常菜", "游泳", "逛美术馆"),
        personality_tags=("喜欢独处", "直接坦率", "做事有计划", "遇事不慌"),
        photos=("anime:028", "portrait:profile-man-alt.webp"),
        single_reason="读书、工作一路按计划走，感情这件事反而没有计划。",
        family_background="父母在常州，都是普通职员，家庭关系简单。",
        house="已购房", car="已购车",
        dating_goal="倾向结婚", meeting_pace="可以接受直接见面聊",
        extra_requirement="希望对方在南京或周边，周末能稳定见面。",
        age_range=(25, 34), height_range=(156, 174), income_min=9000,
        preferred_province_code="320000", preferred_city_codes=("320100", "320400"),
        custom_tag="周末露营党", custom_tag_category="travel_outdoor",
    ),
    _member(
        phone="13800001013", nickname="何知遥", real_name="何知遥", gender=2,
        birthday="1998-01-22", avatar="anime:015", id_card="310105199801220042",
        city="上海", city_code="310100", district_code="310105",
        hometown="上海", hometown_code="310100",
        occupation="小学语文老师", industry="教育", company="上海市长宁区某小学",
        education_level=3, school="华东师范大学",
        height=160, weight=49, income=14000, mbti="ESFP",
        intro="小学语文老师，每天和一群小朋友打交道，练出了很有耐心的脾气。喜欢话剧和音乐剧，也喜欢带学生一起排课本剧。",
        love_view="关系里要能一起笑，也要能一起安静地待着。",
        ideal_partner="希望对方体贴、愿意表达，不会把情绪都憋在心里。",
        hobbies="话剧、音乐剧、读书、旅行",
        interest_tags=("看话剧", "看音乐剧", "逛创意市集", "公园野餐"),
        personality_tags=("外向开朗", "温柔细心", "爱笑", "懂得倾听"),
        photos=("anime:019", "portrait:profile-woman-community.webp"),
        single_reason="圈子比较小，同事朋友大多是女生。",
        family_background="上海本地家庭，父母都是教师，家庭氛围温和。",
        house="与家人同住",
        dating_goal="倾向恋爱", meeting_pace="一周内可以线下见面",
        children_intention="愿意要孩子",
        extra_requirement="希望对方在上海工作生活，有稳定的作息。",
        age_range=(26, 35), height_range=(172, 186), income_min=10000,
        preferred_province_code="310000", preferred_city_codes=("310100",),
        membership="monthly",
    ),
    _member(
        phone="13800001014", nickname="江砚", real_name="江砚", gender=1,
        birthday="1996-04-30", avatar="anime:030", id_card="310104199604300035",
        city="上海", city_code="310100", district_code="310104",
        hometown="宁波", hometown_code="330200",
        occupation="游戏策划", industry="互联网", company="上海某游戏研发工作室",
        education_level=4, school="同济大学",
        height=177, weight=72, income=28000, mbti="ENTP",
        intro="做游戏关卡策划，习惯站在玩家角度想问题。生活里爱琢磨新鲜事，最近在学做菜和拍胶片，也喜欢跟人讨论各种没用的有趣问题。",
        love_view="好的关系让人放松，不必一直表现得很厉害。",
        ideal_partner="希望对方想法多、不无聊，也接受我有时候会较真。",
        hobbies="桌游、剧本杀、拍胶片、做饭",
        interest_tags=("桌游", "剧本杀", "拍胶片", "复刻餐厅菜"),
        personality_tags=("脑洞大", "有幽默感", "喜欢尝试新事物", "直接坦率"),
        photos=("anime:032", "portrait:profile-man-light.webp"),
        single_reason="之前项目上线连轴转，社交圈基本只剩同事。",
        family_background="父母在宁波做小生意，家里比较务实。",
        dating_goal="倾向恋爱", meeting_pace="先约一场轻松的活动",
        extra_requirement="希望对方理解互联网行业的工作强度，也愿意留时间给关系。",
        age_range=(24, 33), height_range=(155, 173), income_min=8000,
        preferred_province_code="310000", preferred_city_codes=("310100", "330200"),
    ),
    _member(
        phone="13800001015", nickname="邵以宁", real_name="邵以宁", gender=2,
        birthday="1997-09-05", avatar="anime:021", id_card="330102199709050048",
        city="杭州", city_code="330100", district_code="330102",
        hometown="嘉兴", hometown_code="330400",
        occupation="平面设计师", industry="设计服务", company="杭州白露品牌设计工作室",
        education_level=3, school="浙江理工大学",
        height=164, weight=50, income=15000, mbti="INFP",
        intro="在独立品牌工作室做平面设计，每天和字体、颜色打交道。喜欢安静的地方，也喜欢看不同的城市怎么生活，节假日会带着相机去小城住几天。",
        love_view="希望关系里有了解，也有各自的空间。",
        ideal_partner="希望对方温和、有审美、愿意一起去看展和旅行。",
        hobbies="画画、摄影、旅行、逛美术馆",
        interest_tags=("画插画", "逛美术馆", "去小城住几天", "探咖啡店"),
        personality_tags=("慢热", "喜欢独处", "温柔细心", "重视仪式感"),
        photos=("anime:023", "portrait:profile-woman-alt.webp"),
        single_reason="性格偏安静，不太会主动进入新的社交圈。",
        family_background="父母在嘉兴，家里比较尊重我的选择。",
        dating_goal="倾向恋爱", meeting_pace="先线上熟悉，再约线下",
        extra_requirement="希望对方能接受我周末比较宅，也愿意偶尔陪我出门。",
        age_range=(26, 35), height_range=(170, 186), income_min=9000,
        preferred_province_code="330000", preferred_city_codes=("330100",),
        custom_tag="周末逛市集", custom_tag_category="leisure",
    ),
    _member(
        phone="13800001016", nickname="秦朗", real_name="秦朗", gender=1,
        birthday="1994-11-27", avatar="anime:025", id_card="330106199411270019",
        city="杭州", city_code="330100", district_code="330106",
        hometown="温州", hometown_code="330300",
        occupation="软件测试工程师", industry="互联网", company="杭州某互联网公司",
        education_level=3, school="杭州电子科技大学",
        height=179, weight=75, income=22000, mbti="ESTJ",
        intro="做软件测试工程师，工作里最擅长发现别人忽略的细节。离婚后一个人住在杭州，生活规律，周末会去打球或者开车去周边城市转转。",
        love_view="经历过一段婚姻，更清楚沟通和边界有多重要。",
        ideal_partner="希望对方务实、不回避问题，我们能坦诚地把话说清楚。",
        hobbies="球类、自驾游、看车评、做饭",
        interest_tags=("打篮球", "自驾游", "看车评", "周末山路自驾"),
        personality_tags=("直接坦率", "遇事不慌", "说到做到", "有边界感"),
        photos=("anime:025", "portrait:profile-man-sea.webp"),
        single_reason="上一段婚姻在 2022 年结束，现在状态和心态都稳定了。",
        family_background="父母在温州，对我的再婚没有给压力。",
        house="已购房", car="已购车",
        dating_goal="倾向结婚", meeting_pace="可以接受直接见面聊",
        children_intention="愿意要孩子",
        extra_requirement="希望对方能接受离异经历，也愿意一起面对双方家庭。",
        age_range=(28, 38), height_range=(156, 174), education_min=2, income_min=10000,
        preferred_province_code="330000", preferred_city_codes=("330100",),
        is_married=2,
        membership="quarterly",
    ),
    # C 端主测试账号（账号一）：同时是红娘工作台账号，补齐后 C 端门禁才全部打开。
    _member(
        phone=PRIMARY_VIEWER_PHONE, nickname="账号一·测试", real_name="邱嘉诚", gender=1,
        birthday="1993-06-15", avatar="portrait:profile-man-light.webp", id_card="320102199306150019",
        city="北京", city_code="110100", district_code="110105",
        hometown="北京", hometown_code="110100",
        occupation="产品负责人", industry="互联网", company="本地演示科技有限公司",
        education_level=4, school="东南大学",
        height=173, weight=68, income=30000, mbti="ENFJ",
        intro="在产品岗位上工作多年，习惯把复杂的问题拆开再看。周末喜欢跑步和看电影，也在学着把生活节奏放慢一点。",
        love_view="希望关系里两个人都能保持自我，同时愿意为对方调整。",
        ideal_partner="希望对方独立、愿意沟通，能一起讨论长期的生活安排。",
        hobbies="跑步、电影、阅读、做饭",
        interest_tags=("城市漫步", "看悬疑片", "研究家常菜", "跑马拉松"),
        personality_tags=("情绪稳定", "说到做到", "直接坦率", "做事有计划"),
        photos=("anime:002", "anime:008"),
        house="已购房", car="已购车",
        dating_goal="倾向结婚", meeting_pace="先线上熟悉，再约线下",
        children_intention="愿意要孩子",
        extra_requirement="希望双方对长期生活有共同规划。",
        age_range=(24, 40), height_range=(150, 195), education_min=1, income_min=0,
        preferred_province_code=None, preferred_city_codes=(),
        accept_long_distance=1,
        membership="yearly", boost_active=True,
    ),
)


def _slug(phone: str) -> str:
    return f"demo-{phone}"


MEMBER_BY_PHONE: dict[str, dict[str, Any]] = {item["phone"]: item for item in MEMBERS}


def _connect() -> Any:
    import pymysql
    from database_setup_marriage import get_db_config

    return pymysql.connect(
        **get_db_config(),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _assert_environment(environment: str | None) -> None:
    """Refuse to run anywhere but a local development/testing database."""
    env = (environment or os.getenv("ENV") or os.getenv("ENVIRONMENT") or "development").lower()
    if env not in {"development", "testing"}:
        raise RuntimeError("会员演示数据只允许在 development/testing 环境执行")


# --------------------------------------------------------------------------- #
# 素材生成
# --------------------------------------------------------------------------- #


def _asset_source(token: str) -> Path:
    if token.startswith("anime:"):
        return FRONTEND_STATIC / "avatars" / f"matchmaking-anime-{token.split(':', 1)[1]}.jpg"
    if token.startswith("portrait:"):
        return FRONTEND_STATIC / "portraits" / token.split(":", 1)[1]
    raise ValueError(f"未知素材标记：{token}")


def _render_image(source: Path, directory: Path, name: str, *, thumbnail: bool) -> dict[str, Any]:
    """Convert one source image to webp (+thumbnail) and return URLs and sizes."""
    from PIL import Image, ImageOps

    directory.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as raw:
        image = ImageOps.exif_transpose(raw)
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGB")
        image_path = directory / f"{name}.webp"
        image.save(image_path, format="WEBP", quality=85, method=4)
        thumb_url = None
        if thumbnail:
            copy = image.copy()
            copy.thumbnail((480, 480), Image.Resampling.LANCZOS)
            thumb_path = directory / f"{name}-thumb.webp"
            copy.save(thumb_path, format="WEBP", quality=80, method=4)
            thumb_url = f"/storage/uploads/{directory.name}/{thumb_path.name}"
    return {
        "url": f"/storage/uploads/{directory.name}/{image_path.name}",
        "thumb": thumb_url,
        "size": image_path.stat().st_size,
    }


def _render_certificate(directory: Path, name: str, title: str, lines: tuple[str, ...]) -> dict[str, Any]:
    """Render a believable demo certificate image for the certification pages."""
    from PIL import Image, ImageDraw, ImageFont

    directory.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", (1000, 700), (248, 246, 241))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((30, 30, 970, 670), outline=(196, 176, 146), width=3)
    draw.rectangle((46, 46, 954, 654), outline=(216, 202, 178), width=1)

    def font(size: int) -> Any:
        for candidate in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
        return ImageFont.load_default()

    draw.text((500, 168), title, fill=(56, 50, 42), font=font(48), anchor="mm")
    draw.line((370, 216, 630, 216), fill=(196, 176, 146), width=2)
    y = 288
    for line in lines:
        draw.text((500, y), line, fill=(94, 86, 74), font=font(27), anchor="mm")
        y += 48
    draw.text(
        (500, 588), "本地开发环境演示材料 · 不代表任何真实证件", fill=(170, 160, 144),
        font=font(20), anchor="mm",
    )
    path = directory / f"{name}.webp"
    canvas.save(path, format="WEBP", quality=88, method=4)
    return {"url": f"/storage/uploads/{directory.name}/{path.name}", "size": path.stat().st_size}


def _prepare_assets(member: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Create every image this member needs and return their public URLs."""
    directory = Path("storage/uploads") / str(user_id)
    tag = _slug(member["phone"])
    avatar = _render_image(_asset_source(member["avatar"]), directory, f"{tag}-avatar", thumbnail=True)
    album = [
        _render_image(_asset_source(extra), directory, f"{tag}-photo-{index}", thumbnail=True)
        for index, extra in enumerate(member["photos"], start=1)
    ]
    background = _render_image(_asset_source(member["photos"][0]), directory, f"{tag}-background", thumbnail=False)
    certificates = {
        "id_front": _render_certificate(
            directory, f"{tag}-id-front", "居民身份证（正面）",
            (f"姓名：{member['real_name']}", f"住址：{member['city']}演示地址", "本地演示材料"),
        ),
        "id_back": _render_certificate(
            directory, f"{tag}-id-back", "居民身份证（反面）",
            ("签发机关：演示公安局", "有效期限：长期", "本地演示材料"),
        ),
        "face": _render_certificate(
            directory, f"{tag}-face", "人脸核验留档",
            (f"核验人：{member['real_name']}", "核验方式：动作活检", "比对结果：通过（98.50）"),
        ),
        "education": _render_certificate(
            directory, f"{tag}-education", "学历证书",
            (
                f"姓名：{member['real_name']}",
                f"学历：{_EDUCATION_DEGREE[member['education_level']]}",
                f"毕业院校：{member['school']}",
            ),
        ),
        "job": _render_certificate(
            directory, f"{tag}-job", "在职证明",
            (f"姓名：{member['real_name']}", f"单位：{member['company']}", f"职务：{member['occupation']}"),
        ),
        "house": _render_certificate(
            directory, f"{tag}-house", "不动产权证书",
            (f"权利人：{member['real_name']}", f"坐落：{member['city']}演示小区", "共有情况：单独所有"),
        ),
        "marriage": _render_certificate(
            directory, f"{tag}-marriage", "婚姻状况声明",
            (
                f"姓名：{member['real_name']}",
                "声明：本人目前无婚姻登记记录" if member["is_married"] == 1 else "声明：本人为离异状态",
                "本地演示材料",
            ),
        ),
        "pledge": _render_certificate(
            directory, f"{tag}-pledge", "单身承诺书签名",
            (f"签署人：{member['nickname']}", "承诺：所登记资料真实、准确", "本地演示材料"),
        ),
        "extra": _render_certificate(
            directory, f"{tag}-income", "收入证明",
            (f"姓名：{member['real_name']}", f"任职单位：{member['company']}", "月收入：已核验"),
        ),
    }
    return {"avatar": avatar, "album": album, "background": background, "certificates": certificates}


# --------------------------------------------------------------------------- #
# 数据写入
# --------------------------------------------------------------------------- #


def _ensure_user(cur: Any, member: dict[str, Any], now: datetime) -> tuple[int, bool]:
    """Create or refresh ``users``; the avatar is set later once assets exist."""
    from app.core.security import hash_password

    cur.execute("SELECT id FROM users WHERE phone=%s LIMIT 1", (member["phone"],))
    row = cur.fetchone()
    password = hash_password(DEMO_PASSWORD)
    if row:
        user_id = int(row["id"])
        cur.execute(
            """UPDATE users SET nickname=%s, gender=%s, birthday=%s, status=1, is_real_name=1,
            is_married=%s, is_single_pledge=1, risk_status=0,
            phone_verified_at=COALESCE(phone_verified_at, UTC_TIMESTAMP()),
            openid=COALESCE(NULLIF(openid,''), %s), password=%s,
            register_ip='127.0.0.1', updated_at=%s
            WHERE id=%s""",
            (
                member["nickname"], member["gender"], member["birthday_date"], member["is_married"],
                _slug(member["phone"]), password, now, user_id,
            ),
        )
        return user_id, False
    cur.execute(
        """INSERT INTO users (openid, phone, phone_verified_at, nickname, gender, birthday,
        status, is_real_name, is_married, is_single_pledge, data_complete_rate, register_ip,
        password, created_at, updated_at)
        VALUES (%s,%s,UTC_TIMESTAMP(),%s,%s,%s,1,1,%s,1,0,'127.0.0.1',%s,%s,%s)""",
        (
            _slug(member["phone"]), member["phone"], member["nickname"], member["gender"],
            member["birthday_date"], member["is_married"], password, now, now,
        ),
    )
    return int(cur.lastrowid), True


def _upsert_profile(cur: Any, member: dict[str, Any], user_id: int, assets: dict[str, Any], now: datetime) -> None:
    tags: Any = []
    if member.get("custom_tag"):
        tags = {
            "custom": [member["custom_tag"]],
            "custom_categories": {member["custom_tag"]: member["custom_tag_category"]},
        }
    album_urls = [item["url"] for item in assets["album"]]
    cur.execute(
        """INSERT INTO user_profile
        (user_id, height, weight, income, hometown, residence, mbti, constellation, zodiac, tags,
         self_intro, love_view, ideal_partner, single_reason, family_background, hobbies, photos,
         occupation, industry, education_level, hometown_province_code, hometown_city_code,
         residence_province_code, residence_city_code, residence_district_code, household,
         ethnicity, house, car, smoking, community_city_name, community_city_code,
         community_city_updated_at, online_status, last_active_at, interest_tags,
         personality_tags, location_consent, location_visible, location_source, location_updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,'汉族',%s,%s,%s,%s,%s,%s,1,%s,%s,%s,1,1,'manual',%s)
        ON DUPLICATE KEY UPDATE height=VALUES(height), weight=VALUES(weight), income=VALUES(income),
        hometown=VALUES(hometown), residence=VALUES(residence), mbti=VALUES(mbti),
        constellation=VALUES(constellation), zodiac=VALUES(zodiac), tags=VALUES(tags),
        self_intro=VALUES(self_intro), love_view=VALUES(love_view), ideal_partner=VALUES(ideal_partner),
        single_reason=VALUES(single_reason), family_background=VALUES(family_background),
        hobbies=VALUES(hobbies), photos=VALUES(photos), occupation=VALUES(occupation),
        industry=VALUES(industry), education_level=VALUES(education_level),
        hometown_province_code=VALUES(hometown_province_code), hometown_city_code=VALUES(hometown_city_code),
        residence_province_code=VALUES(residence_province_code),
        residence_city_code=VALUES(residence_city_code),
        residence_district_code=VALUES(residence_district_code), household=VALUES(household),
        house=VALUES(house), car=VALUES(car), smoking=VALUES(smoking),
        community_city_name=VALUES(community_city_name), community_city_code=VALUES(community_city_code),
        community_city_updated_at=VALUES(community_city_updated_at), online_status=1,
        last_active_at=VALUES(last_active_at), interest_tags=VALUES(interest_tags),
        personality_tags=VALUES(personality_tags), location_consent=1, location_visible=1,
        location_source='manual', location_updated_at=VALUES(location_updated_at)""",
        (
            user_id, member["height"], member["weight"], member["income"], member["hometown"],
            member["city"], member["mbti"], _constellation(member["birthday_date"]),
            _zodiac(member["birthday_date"]), json.dumps(tags, ensure_ascii=False),
            member["intro"], member["love_view"], member["ideal_partner"], member["single_reason"],
            member["family_background"], member["hobbies"], json.dumps(album_urls, ensure_ascii=False),
            member["occupation"], member["industry"], member["education_level"],
            member["hometown_province_code"], member["hometown_code"], member["province_code"],
            member["city_code"], member["district_code"], f"{member['hometown']}市",
            member["house"], member["car"], member["smoking"], member["city"], member["city_code"],
            now, now, json.dumps(member["interest_tags"], ensure_ascii=False),
            json.dumps(member["personality_tags"], ensure_ascii=False), now,
        ),
    )


def _upsert_preference(cur: Any, member: dict[str, Any], user_id: int) -> None:
    cur.execute(
        """INSERT INTO user_partner_preference
        (user_id, age_min, age_max, height_min, height_max, education_min, income_min,
         marriage_status, preferred_province_code, preferred_city_codes, accept_long_distance,
         accept_cross_province, housing_requirement, smoking_requirement, drinking_requirement,
         extra_requirement, dating_goal, meeting_pace, children_intention, preferred_occupation)
        VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,1,0,1,0,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE age_min=VALUES(age_min), age_max=VALUES(age_max),
        height_min=VALUES(height_min), height_max=VALUES(height_max),
        education_min=VALUES(education_min), income_min=VALUES(income_min),
        preferred_province_code=VALUES(preferred_province_code),
        preferred_city_codes=VALUES(preferred_city_codes),
        accept_long_distance=VALUES(accept_long_distance),
        extra_requirement=VALUES(extra_requirement), dating_goal=VALUES(dating_goal),
        meeting_pace=VALUES(meeting_pace), children_intention=VALUES(children_intention),
        preferred_occupation=VALUES(preferred_occupation)""",
        (
            user_id, member["age_range"][0], member["age_range"][1], member["height_range"][0],
            member["height_range"][1], member["education_min"], member["income_min"],
            member["preferred_province_code"],
            json.dumps(list(member["preferred_city_codes"]), ensure_ascii=False),
            member["accept_long_distance"], member["extra_requirement"], member["dating_goal"],
            member["meeting_pace"], member["children_intention"], member["preferred_occupation"],
        ),
    )


def _upsert_privacy(cur: Any, user_id: int) -> None:
    cur.execute(
        """INSERT INTO user_privacy
        (user_id, hide_phone, hide_school, hide_company, hide_distance, hide_online_status,
         show_profile, show_likes, show_posts, only_auth_can_contact, only_vip_can_see_detail,
         who_can_see_me, match_status, notify_like, notify_comment, notify_match, notify_apply,
         notify_system, notify_activity, notify_message, notify_follow, profile_visibility,
         message_privacy, anonymous_browse_enabled, privacy_version, privacy_updated_at)
        VALUES (%s,1,0,0,0,0,1,1,1,0,0,1,1,1,1,1,1,1,1,1,1,'all','all',1,'v1',UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE show_profile=1, show_likes=1, show_posts=1, match_status=1,
        who_can_see_me=1, profile_visibility='all', message_privacy='all',
        anonymous_browse_enabled=1, hide_phone=1, hide_school=0, hide_company=0,
        privacy_version='v1', privacy_updated_at=UTC_TIMESTAMP()""",
        (user_id,),
    )


def _upsert_auth(cur: Any, member: dict[str, Any], user_id: int, certs: dict[str, Any]) -> None:
    from app.core.security import encrypt_sensitive, mask_id_card

    card = member["id_card"]
    cur.execute(
        """INSERT INTO user_auth
        (user_id, real_name, id_card, id_card_hash, id_card_masked, id_card_front, id_card_back,
         face_photo, face_verified, face_method, face_vendor, face_score, id_card_issued,
         education, school, education_cert, education_verified, education_submitted_at,
         education_reviewed_at, job, company, job_cert, job_verified, house_cert, house_verified,
         house_submitted_at, house_reviewed_at, marriage_cert, marriage_verified,
         marriage_submitted_at, marriage_reviewed_at, realname_status, realname_provider,
         auth_status, auth_step, submitted_at, verified_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,'动作活检','aliyun_face',98.50,%s,
                %s,%s,%s,2,UTC_TIMESTAMP(),UTC_TIMESTAMP(),%s,%s,%s,1,%s,2,
                UTC_TIMESTAMP(),UTC_TIMESTAMP(),%s,2,UTC_TIMESTAMP(),UTC_TIMESTAMP(),
                2,'aliyun_realname',2,4,UTC_TIMESTAMP(),UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE real_name=VALUES(real_name), id_card=VALUES(id_card),
        id_card_hash=VALUES(id_card_hash), id_card_masked=VALUES(id_card_masked),
        id_card_front=VALUES(id_card_front), id_card_back=VALUES(id_card_back),
        face_photo=VALUES(face_photo), face_verified=1, face_method='动作活检',
        face_vendor='aliyun_face', face_score=98.50, id_card_issued=VALUES(id_card_issued),
        education=VALUES(education), school=VALUES(school), education_cert=VALUES(education_cert),
        education_verified=2, education_fail_reason=NULL, education_submitted_at=UTC_TIMESTAMP(),
        education_reviewed_at=UTC_TIMESTAMP(), job=VALUES(job), company=VALUES(company),
        job_cert=VALUES(job_cert), job_verified=1, house_cert=VALUES(house_cert), house_verified=2,
        house_fail_reason=NULL, house_submitted_at=UTC_TIMESTAMP(),
        house_reviewed_at=UTC_TIMESTAMP(), marriage_cert=VALUES(marriage_cert),
        marriage_verified=2, marriage_fail_reason=NULL, marriage_submitted_at=UTC_TIMESTAMP(),
        marriage_reviewed_at=UTC_TIMESTAMP(), realname_status=2,
        realname_provider='aliyun_realname', auth_status=2, auth_step=4,
        verified_at=UTC_TIMESTAMP(), revoked_at=NULL, revoked_reason=NULL,
        updated_at=UTC_TIMESTAMP()""",
        (
            user_id, member["real_name"], encrypt_sensitive(card),
            hashlib.sha256(card.upper().encode()).hexdigest(), mask_id_card(card),
            certs["id_front"]["url"], certs["id_back"]["url"], certs["face"]["url"],
            "杭州市公安局", _EDUCATION_DEGREE[member["education_level"]], member["school"],
            certs["education"]["url"], member["occupation"], member["company"],
            certs["job"]["url"], certs["house"]["url"],
            "user_confirmed_unmarried" if member["is_married"] == 1 else certs["marriage"]["url"],
        ),
    )


def _upsert_commitment(cur: Any, member: dict[str, Any], user_id: int, material: str, now: datetime) -> None:
    content = (
        f"本人使用网名{member['nickname']}，编号：G{user_id:06d}，在宣誓爱登记婚恋资料。"
        "本人承诺所登记资料真实、准确，当前婚姻状态为单身；如状态发生变化，将及时更新资料，"
        "并自行承担信息不实造成的相应责任。"
    )
    cur.execute("SELECT id FROM user_commitment_sign WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            """UPDATE user_commitment_sign SET title='单身承诺', content=%s, file_url=%s, status=1,
            remark=NULL, reviewed_at=UTC_TIMESTAMP(), updated_at=UTC_TIMESTAMP() WHERE id=%s""",
            (content, material, int(row["id"])),
        )
        return
    cur.execute(
        """INSERT INTO user_commitment_sign
        (user_id, title, content, file_url, sign_times, status, reviewed_at, created_at, updated_at)
        VALUES (%s,'单身承诺',%s,%s,1,1,UTC_TIMESTAMP(),%s,%s)""",
        (user_id, content, material, now, now),
    )


def _approve_pending_media(cur: Any, user_id: int) -> int:
    """Approve this member's leftover pending uploads.

    可见性门禁要求作者没有任何 ``review_status IN (0,2,3)`` 的媒体，否则
    该会员不会出现在任何人的推荐/搜索里。本地开发环境没有审核后台，
    历史上传会一直停在待审，因此这里显式补一次"审核通过"。
    仅作用于演示会员自己的媒体行。
    """
    cur.execute(
        """UPDATE user_media SET review_status=1, reviewed_at=UTC_TIMESTAMP(),
        review_reason=NULL WHERE user_id=%s AND deleted_at IS NULL AND review_status <> 1""",
        (user_id,),
    )
    return cur.rowcount


def _replace_media(cur: Any, member: dict[str, Any], user_id: int, assets: dict[str, Any]) -> None:
    """Replace this member's own demo media rows (only rows this script created)."""
    cur.execute(
        "DELETE FROM user_media WHERE user_id=%s AND storage_key LIKE %s",
        (user_id, f"{DEMO_SLUG}/%"),
    )
    cur.execute(
        """INSERT INTO user_media
        (user_id, media_type, file_url, storage_key, thumbnail_url, mime_type, file_size,
         sort_order, is_primary, review_status, reviewed_at)
        VALUES (%s,'avatar',%s,%s,%s,'image/webp',%s,0,1,1,UTC_TIMESTAMP())""",
        (
            user_id, assets["avatar"]["url"], f"{DEMO_SLUG}/{user_id}/avatar",
            assets["avatar"]["thumb"], assets["avatar"]["size"],
        ),
    )
    for index, item in enumerate(assets["album"], start=1):
        cur.execute(
            """INSERT INTO user_media
            (user_id, media_type, file_url, storage_key, thumbnail_url, mime_type, file_size,
             sort_order, is_primary, review_status, reviewed_at)
            VALUES (%s,'photo',%s,%s,%s,'image/webp',%s,%s,0,1,UTC_TIMESTAMP())""",
            (
                user_id, item["url"], f"{DEMO_SLUG}/{user_id}/photo-{index}",
                item["thumb"], item["size"], index,
            ),
        )
    cur.execute(
        """INSERT INTO user_media
        (user_id, media_type, file_url, storage_key, thumbnail_url, mime_type, file_size,
         sort_order, is_primary, review_status, reviewed_at)
        VALUES (%s,'background',%s,%s,NULL,'image/webp',%s,0,0,1,UTC_TIMESTAMP())""",
        (
            user_id, assets["background"]["url"], f"{DEMO_SLUG}/{user_id}/background",
            assets["background"]["size"],
        ),
    )
    _approve_pending_media(cur, user_id)


def _refresh_completion(cur: Any, user_id: int) -> float:
    """Recompute completion with the production rule weights and persist it."""
    from app.services.profile import COMPLETION_RULES

    cur.execute(
        """SELECT u.gender, u.birthday, u.is_married, u.is_single_pledge, u.avatar,
        COALESCE(ua.realname_status, 0) AS realname_status, p.occupation, p.education_level,
        p.income, p.height, p.weight, p.self_intro, p.hometown_province_code, p.hometown_city_code,
        p.residence_province_code, p.residence_city_code, p.interest_tags, p.personality_tags,
        p.mbti, p.tags, pref.age_min, pref.age_max,
        EXISTS (SELECT 1 FROM user_media m WHERE m.user_id = u.id AND m.media_type='photo'
                AND m.deleted_at IS NULL) AS album_done
        FROM users u
        LEFT JOIN user_profile p ON p.user_id = u.id
        LEFT JOIN user_partner_preference pref ON pref.user_id = u.id
        LEFT JOIN user_auth ua ON ua.user_id = u.id
        WHERE u.id=%s""",
        (user_id,),
    )
    row = cur.fetchone()

    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            return list(parsed) if isinstance(parsed, list) else []
        return []

    raw_tags = as_list(row["interest_tags"]) + as_list(row["personality_tags"]) + as_list(row["tags"])
    age = _age_of(row["birthday"]) if row["birthday"] else 0
    completed = {
        "gender": row["gender"] in (1, 2),
        "birthday": bool(row["birthday"] and age >= 18),
        "location": bool(row["residence_province_code"] and row["residence_city_code"]),
        "marriage": row["is_married"] in (1, 2, 3),
        "occupation": bool(row["occupation"]),
        "education": row["education_level"] is not None,
        "income": row["income"] is not None,
        "height": row["height"] is not None,
        "weight": row["weight"] is not None,
        "hometown": bool(row["hometown_province_code"] and row["hometown_city_code"]),
        "avatar": bool(row["avatar"]),
        "intro": bool(row["self_intro"] and len(str(row["self_intro"]).strip()) >= 20),
        "album": bool(row["album_done"]),
        "personal_tags": len(raw_tags) >= 3,
        "mbti": bool(row["mbti"]),
        "preference": row["age_min"] is not None and row["age_max"] is not None,
        "realname": int(row["realname_status"] or 0) == 2,
        "single_pledge": int(row["is_single_pledge"] or 0) == 1,
    }
    score = float(sum(weight for key, _, weight in COMPLETION_RULES if completed[key]))
    # 列名与规则键并非一一对应（interest_completed 承载 personal_tags），
    # 因此沿用生产 recalculate_completion 的显式映射，避免写出不存在的列。
    columns = {
        "gender_completed": completed["gender"],
        "birthday_completed": completed["birthday"],
        "location_completed": completed["location"],
        "marriage_completed": completed["marriage"],
        "occupation_completed": completed["occupation"],
        "education_completed": completed["education"],
        "income_completed": completed["income"],
        "height_completed": completed["height"],
        "weight_completed": completed["weight"],
        "hometown_completed": completed["hometown"],
        "avatar_completed": completed["avatar"],
        "intro_completed": completed["intro"],
        "album_completed": completed["album"],
        "interest_completed": completed["personal_tags"],
        "preference_completed": completed["preference"],
        "realname_completed": completed["realname"],
        "mbti_completed": completed["mbti"],
        "single_pledge_completed": completed["single_pledge"],
    }
    names = list(columns)
    cur.execute(
        f"""INSERT INTO user_profile_completion
        (user_id, {', '.join(names)}, score, algorithm_version, calculated_at)
        VALUES (%s, {', '.join(['%s'] * len(names))}, %s, 'profile-v4', UTC_TIMESTAMP())
        ON DUPLICATE KEY UPDATE
        {', '.join(f'{name}=VALUES({name})' for name in names)},
        score=VALUES(score), algorithm_version='profile-v4', calculated_at=UTC_TIMESTAMP()""",
        (user_id, *[int(value) for value in columns.values()], score),
    )
    cur.execute("UPDATE users SET data_complete_rate=%s WHERE id=%s", (int(score), user_id))
    return score


def _upsert_role_intent_agreements(cur: Any, member: dict[str, Any], user_id: int, now: datetime) -> None:
    cur.execute(
        """INSERT INTO user_role (user_id, role_code, status) VALUES (%s,'user',1)
        ON DUPLICATE KEY UPDATE status=1, revoked_at=NULL, revoke_reason=NULL""",
        (user_id,),
    )
    cur.execute(
        """INSERT INTO user_registration_intent
        (user_id, intent_type, source, version, status, selected_at, revoked_at)
        VALUES (%s,%s,'profile','v1',1,%s,NULL)
        ON DUPLICATE KEY UPDATE intent_type=VALUES(intent_type), status=1,
        selected_at=VALUES(selected_at), revoked_at=NULL""",
        (user_id, member["intent"], now),
    )
    for agreement, scene in (
        ("user_service", "register"),
        ("privacy_policy", "register"),
        ("community_rules", "community"),
        ("safety_pledge", "certification"),
    ):
        cur.execute(
            """INSERT IGNORE INTO user_agreement_acceptance
            (user_id, agreement_type, agreement_version, accepted_ip, scene, status)
            VALUES (%s,%s,'v1','127.0.0.1',%s,1)""",
            (user_id, agreement, scene),
        )


def _upsert_points(cur: Any, user_id: int, now: datetime, index: int) -> None:
    cur.execute(
        "SELECT id FROM user_points WHERE user_id=%s AND `desc` LIKE %s LIMIT 1",
        (user_id, "演示数据初始化%"),
    )
    if cur.fetchone():
        return
    entries = (
        (1, 5, "每日签到"),
        (2, 50, "完成任务：完成资料"),
        (2, 100, "完成任务：完成实名认证"),
        (3, 60, "邀请好友注册奖励"),
        (2, 300 + index * 10, "演示数据初始化积分"),
    )
    balance = 0
    for order, (kind, amount, description) in enumerate(entries):
        balance += amount
        cur.execute(
            """INSERT INTO user_points (user_id, type, amount, balance, `desc`, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, kind, amount, balance, description, now - timedelta(days=len(entries) - order)),
        )
    for task_code, reward in (("profile_complete", 50), ("realname_verified", 100)):
        cur.execute(
            """INSERT INTO user_task (user_id, task_code, status, reward, completed_at, created_at)
            VALUES (%s,%s,1,%s,%s,%s)
            ON DUPLICATE KEY UPDATE status=1, reward=VALUES(reward), completed_at=VALUES(completed_at)""",
            (user_id, task_code, f"{reward}积分", now - timedelta(days=1), now - timedelta(days=1)),
        )
    for offset in range(1, 7):
        moment = now - timedelta(days=offset)
        cur.execute(
            """INSERT INTO user_checkin (user_id, checkin_date, continuous_days, points, created_at)
            VALUES (%s,%s,%s,5,%s)
            ON DUPLICATE KEY UPDATE continuous_days=VALUES(continuous_days), points=5""",
            (user_id, moment.date(), 7 - offset, moment),
        )


def _upsert_membership_wallet(
    cur: Any, member: dict[str, Any], user_id: int, now: datetime, boost_package_id: int | None
) -> None:
    code = member.get("membership")
    if code:
        cur.execute("SELECT id, price, duration_days FROM config_membership_package WHERE code=%s", (code,))
        package = cur.fetchone()
        cur.execute(
            "SELECT id FROM user_membership WHERE user_id=%s AND order_no=%s LIMIT 1",
            (user_id, f"DEMO-VIP-{user_id}"),
        )
        if package and not cur.fetchone():
            cur.execute(
                """INSERT INTO user_membership
                (user_id, package_type, amount, order_no, start_at, end_at, status)
                VALUES (%s,%s,%s,%s,%s,%s,1)""",
                (
                    user_id, code, package["price"], f"DEMO-VIP-{user_id}", now - timedelta(days=3),
                    now + timedelta(days=int(package["duration_days"])),
                ),
            )
    cur.execute(
        "SELECT id FROM account_ledger WHERE account_type='user' AND account_id=%s LIMIT 1",
        (user_id,),
    )
    if not cur.fetchone():
        cur.execute(
            """INSERT INTO account_ledger
            (account_type, account_id, direction, amount, state, source_type, source_id,
             idempotency_key, created_at)
            VALUES ('user',%s,'CREDIT',5000.00,'AVAILABLE','demo_seed',%s,%s,%s)""",
            (user_id, user_id, f"demo-seed-balance-{user_id}", now),
        )
    if member.get("boost_active"):
        # user_boost 没有 order_no 唯一键，因此显式判重后再写，保证幂等。
        cur.execute(
            "SELECT id FROM user_boost WHERE user_id=%s AND order_no=%s LIMIT 1",
            (user_id, f"DEMO-BOOST-{user_id}"),
        )
        if not cur.fetchone():
            cur.execute(
                """INSERT INTO user_boost
                (user_id, target_user_id, amount, order_no, created_at, start_at, end_at, status,
                 pay_status, pay_method)
                VALUES (%s,%s,5.00,%s,%s,%s,%s,1,1,'balance')""",
                (
                    user_id, user_id, f"DEMO-BOOST-{user_id}", now, now - timedelta(hours=2),
                    now + timedelta(days=6),
                ),
            )
        if boost_package_id:
            cur.execute(
                "SELECT id FROM user_exposure WHERE user_id=%s AND order_no=%s LIMIT 1",
                (user_id, f"DEMO-BOOST-{user_id}"),
            )
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO user_exposure
                    (user_id, package_id, order_no, start_at, end_at, status, created_at)
                    VALUES (%s,%s,%s,%s,%s,1,%s)""",
                    (
                        user_id, boost_package_id, f"DEMO-BOOST-{user_id}",
                        now - timedelta(hours=2), now + timedelta(days=6), now,
                    ),
                )


def _upsert_quota(cur: Any, user_id: int, now: datetime) -> None:
    cur.execute(
        """INSERT INTO user_quota_grant (user_id, quota_code, remaining, source, order_no, expires_at)
        VALUES (%s,'apply',3,'points',%s,%s)
        ON DUPLICATE KEY UPDATE remaining=VALUES(remaining), expires_at=VALUES(expires_at)""",
        (user_id, f"DEMO-QUOTA-{user_id}", now + timedelta(days=30)),
    )
    cur.execute(
        """INSERT INTO user_quota_usage (user_id, quota_code, quota_date, source, amount, reason)
        SELECT %s,'browse',CURDATE(),'free',1,'演示数据：今日已浏览 1 次'
        WHERE NOT EXISTS (SELECT 1 FROM user_quota_usage
                          WHERE user_id=%s AND quota_code='browse' AND quota_date=CURDATE())""",
        (user_id, user_id),
    )


def _mbti_scores(mbti: str) -> tuple[int, int, int, int]:
    scores = []
    for index, (left, right) in enumerate(_MBTI_POLES):
        letter = mbti[index] if index < len(mbti) else left
        scores.append(76 if letter == left else 24)
    return (scores[0], scores[1], scores[2], scores[3])


def _upsert_profile_extras(cur: Any, member: dict[str, Any], user_id: int) -> None:
    ei, sn, tf, jp = _mbti_scores(member["mbti"])
    cur.execute(
        """INSERT INTO user_feature_vector
        (user_id, age, gender, height, income_level, education_level, city_code, hometown_city,
         mbti_ei, mbti_sn, mbti_tf, mbti_jp, interest_vector, active_score, response_rate,
         avg_response_time, version)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,88.00,92.00,180,1)
        ON DUPLICATE KEY UPDATE age=VALUES(age), gender=VALUES(gender), height=VALUES(height),
        income_level=VALUES(income_level), education_level=VALUES(education_level),
        city_code=VALUES(city_code), hometown_city=VALUES(hometown_city), mbti_ei=VALUES(mbti_ei),
        mbti_sn=VALUES(mbti_sn), mbti_tf=VALUES(mbti_tf), mbti_jp=VALUES(mbti_jp),
        interest_vector=VALUES(interest_vector), active_score=88.00, response_rate=92.00, version=1""",
        (
            user_id, member["age"], member["gender"], member["height"],
            min(5, max(1, member["income"] // 6000)), member["education_level"],
            member["city_code"], member["hometown_code"], ei, sn, tf, jp,
            json.dumps({tag: 0.8 for tag in member["interest_tags"]}, ensure_ascii=False),
        ),
    )
    cur.execute(
        """INSERT INTO user_mbti_result
        (user_id, mbti_type, ei_score, sn_score, tf_score, jp_score, dimensions, description,
         test_version, test_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'v1',CURDATE())
        ON DUPLICATE KEY UPDATE mbti_type=VALUES(mbti_type), ei_score=VALUES(ei_score),
        sn_score=VALUES(sn_score), tf_score=VALUES(tf_score), jp_score=VALUES(jp_score),
        dimensions=VALUES(dimensions), description=VALUES(description)""",
        (
            user_id, member["mbti"], ei, sn, tf, jp,
            json.dumps({"EI": {"E": 100 - ei, "I": ei}, "SN": {"S": 100 - sn, "N": sn},
                        "TF": {"T": 100 - tf, "F": tf}, "JP": {"J": 100 - jp, "P": jp}},
                       ensure_ascii=False),
            f"{member['mbti']}：在熟悉的关系里更愿意分享，做决定前习惯先想清楚。",
        ),
    )
    cur.execute(
        """INSERT INTO user_love_style_result
        (user_id, attachment_style, love_language, relationship_expectation, test_date)
        VALUES (%s,'安全型',%s,%s,CURDATE())
        ON DUPLICATE KEY UPDATE attachment_style='安全型', love_language=VALUES(love_language),
        relationship_expectation=VALUES(relationship_expectation)""",
        (
            user_id, json.dumps(["精心时刻", "肯定话语"], ensure_ascii=False), member["love_view"],
        ),
    )
    cur.execute(
        """INSERT INTO user_discovery_filter (user_id, filter_json) VALUES (%s,%s)
        ON DUPLICATE KEY UPDATE filter_json=VALUES(filter_json), updated_at=UTC_TIMESTAMP()""",
        (
            user_id,
            json.dumps(
                {
                    "gender": 1 if member["gender"] == 2 else 2,
                    "age_min": member["age_range"][0], "age_max": member["age_range"][1],
                    "education_min": member.get("education_min", 1),
                    "city_code": member["city_code"],
                    "pure_free": False, "page": 1, "page_size": 20,
                },
                ensure_ascii=False,
            ),
        ),
    )


def _upsert_other_certifications(cur: Any, member: dict[str, Any], user_id: int, certs: dict[str, Any]) -> int:
    written = 0
    for name, url in (("收入认证", certs["extra"]["url"]), ("驾驶证认证", certs["extra"]["url"])):
        cur.execute(
            """INSERT INTO user_auth_extra
            (user_id, auth_type_id, auth_type_name, file_url, status, remark, reviewed_at)
            SELECT %s, t.id, t.name, %s, 1, '本地演示：人工已核验', UTC_TIMESTAMP()
            FROM config_auth_type t
            WHERE t.name=%s AND NOT EXISTS (
                SELECT 1 FROM user_auth_extra e WHERE e.user_id=%s AND e.auth_type_id=t.id)""",
            (user_id, url, name, user_id),
        )
        written += cur.rowcount
    cur.execute(
        """INSERT INTO user_marriage_check (user_id, check_method, declared_status, result, cost, checked_at)
        SELECT %s,'线下核查',%s,%s,0.00,UTC_TIMESTAMP()
        WHERE NOT EXISTS (SELECT 1 FROM user_marriage_check WHERE user_id=%s)""",
        (
            user_id, "未婚" if member["is_married"] == 1 else "离异",
            "no_record" if member["is_married"] == 1 else "divorced", user_id,
        ),
    )
    return written


# --------------------------------------------------------------------------- #
# 关系链与互动：让消息 / 通知 / 申请 / 访客 / 收藏 页面都不为空
# --------------------------------------------------------------------------- #

_INTERACTION_PAIRS: tuple[tuple[str, str], ...] = (
    ("13800001001", "13800001002"),
    ("13800001001", "13800001012"),
    ("13998020600", "17870810291"),
    ("13905000870", "13800001014"),
    ("17870810286", "17870810285"),
    ("13800001011", "13800001016"),
    ("13800001013", "17870810291"),
    # 主测试账号（账号一）需要自己的关系链，否则消息页与我的页全为空。
    (PRIMARY_VIEWER_PHONE, "13800001001"),
    (PRIMARY_VIEWER_PHONE, "13998020600"),
    (PRIMARY_VIEWER_PHONE, "13800001011"),
)

_CHAT_SCRIPTS: dict[tuple[str, str], tuple[tuple[bool, str], ...]] = {
    ("13800001001", "13800001002"): (
        (True, "你好呀，看到你也喜欢城市漫步，最近有去什么地方走走吗？"),
        (False, "上周去了武康路，人不算多，走到安福路那边喝了一杯咖啡。"),
        (True, "听起来很舒服，我最近在南京也找到一条很适合散步的老街。"),
        (False, "下次可以交换一下路线，我也想去南京转转。"),
    ),
    ("13800001001", "13800001012"): (
        (True, "你好，看到你也常去逛美术馆，最近有推荐的展吗？"),
        (False, "有的，市美术馆正在做一个建筑摄影展，我上周刚去。"),
        (True, "那我也去看看，正好最近在补摄影相关的书。"),
    ),
    ("13998020600", "17870810291"): (
        (True, "看到你在做公益活动，感觉很有意义。"),
        (False, "谢谢，就是周末带带志愿者，其实挺充实的。"),
        (True, "我之前也参加过流浪动物的志愿活动，很想再找机会参与。"),
        (False, "那正好，下个月有一场社区活动，可以一起来。"),
    ),
    ("13905000870", "13800001014"): (
        (True, "你好，看到你是做游戏策划的，平时玩什么类型的游戏比较多？"),
        (False, "主要是解谜和策略类，工作之外会刻意避开自己做的品类。"),
        (True, "可以理解，我做产品也会有类似的职业病。"),
    ),
    ("17870810286", "17870810285"): (
        (True, "看到你在高校教书，平时课多吗？"),
        (False, "一周六节课，其它时间可以自己安排，节奏还算稳。"),
        (True, "那挺好的，我也希望生活能规律一点。"),
    ),
    ("13800001011", "13800001016"): (
        (True, "你好，看到你也在杭州，周末一般怎么安排？"),
        (False, "天气好就开车去周边，或者在家做饭。你呢？"),
        (True, "我最近在学烘焙，成果还算能吃。"),
        (False, "那下次可以交换一下菜单。"),
    ),
    ("13800001013", "17870810291"): (
        (True, "看到你常去徒步，我最近也想找一个能坚持的运动。"),
        (False, "徒步入门其实不难，从城市周边的短线开始就行。"),
        (True, "那我先试试近郊的路线，谢谢建议。"),
    ),
    (PRIMARY_VIEWER_PHONE, "13800001001"): (
        (True, "你好，看到你也喜欢阅读和城市漫步，最近在读什么书？"),
        (False, "最近在看一本讲城市历史的书，慢慢看，每天读一点。"),
        (True, "我也喜欢这类，下次可以交流一下书单。"),
        (False, "好呀，我整理一份发给你。"),
    ),
    (PRIMARY_VIEWER_PHONE, "13998020600"): (
        (True, "你好，看到你在做心理咨询相关的工作，感觉很有意思。"),
        (False, "谢谢，其实更多的是在学习怎么好好听别人说话。"),
        (True, "这点我也在练习，有时候忍住不给建议挺难的。"),
    ),
    (PRIMARY_VIEWER_PHONE, "13800001011"): (
        (True, "你好，看到你也常跑步，平时一般跑多少公里？"),
        (False, "工作日五公里左右，周末会拉一次长距离。"),
        (True, "那我先跟上五公里的节奏，之后可以约着一起跑。"),
    ),
}


def _seed_relations(cur: Any, ids: dict[str, int], now: datetime) -> dict[str, int]:
    counts = {"matches": 0, "chat_sessions": 0, "chat_messages": 0, "applications": 0, "favorites": 0}
    for index, (left_phone, right_phone) in enumerate(_INTERACTION_PAIRS):
        left, right = ids[left_phone], ids[right_phone]
        lo, hi = min(left, right), max(left, right)
        cur.execute(
            """INSERT INTO user_match (user_id, target_user_id, status, matched_at)
            VALUES (%s,%s,2,%s) ON DUPLICATE KEY UPDATE status=2, updated_at=UTC_TIMESTAMP()""",
            (lo, hi, now - timedelta(days=index + 1)),
        )
        cur.execute(
            """INSERT INTO user_match (user_id, target_user_id, status, matched_at)
            VALUES (%s,%s,2,%s) ON DUPLICATE KEY UPDATE status=2, updated_at=UTC_TIMESTAMP()""",
            (hi, lo, now - timedelta(days=index + 1)),
        )
        counts["matches"] += 2
        script = _CHAT_SCRIPTS.get((left_phone, right_phone), ())
        cur.execute(
            "SELECT id FROM chat_session WHERE user1_id=%s AND user2_id=%s LIMIT 1", (lo, hi)
        )
        session = cur.fetchone()
        if session:
            session_id = int(session["id"])
        else:
            cur.execute(
                """INSERT INTO chat_session
                (user1_id, user2_id, last_message, last_message_time, unread_count_user1,
                 unread_count_user2, created_at, updated_at)
                VALUES (%s,%s,%s,%s,0,1,%s,%s)""",
                (lo, hi, script[-1][1] if script else None, now - timedelta(hours=index + 1),
                 now - timedelta(days=index + 3), now - timedelta(hours=index + 1)),
            )
            session_id = int(cur.lastrowid)
            counts["chat_sessions"] += 1
        for order, (from_left, text) in enumerate(script):
            sender = left if from_left else right
            recipient = right if from_left else left
            cur.execute(
                """SELECT id FROM chat_message WHERE session_id=%s AND from_user_id=%s AND content=%s LIMIT 1""",
                (session_id, sender, text),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO chat_message
                (session_id, from_user_id, to_user_id, type, content, is_read, created_at)
                VALUES (%s,%s,%s,1,%s,%s,%s)""",
                (
                    session_id, sender, recipient, text,
                    1 if order < len(script) - 1 else 0,
                    now - timedelta(hours=(len(script) - order) * 2 + index),
                ),
            )
            counts["chat_messages"] += 1
    # 认识申请：发起方 → 接收方，附带待处理申请（账号一必须收到，否则申请页为空）。
    for index, (from_phone, to_phone) in enumerate(
        (
            ("13800001013", "13905000870"),
            ("13800001015", "13800001002"),
            ("17870810286", "13800001011"),
            ("13800001001", PRIMARY_VIEWER_PHONE),
            ("13800001012", PRIMARY_VIEWER_PHONE),
            (PRIMARY_VIEWER_PHONE, "13800001015"),
        )
    ):
        sender, recipient = ids[from_phone], ids[to_phone]
        cur.execute(
            """SELECT id FROM match_apply WHERE from_user_id=%s AND to_user_id=%s LIMIT 1""",
            (sender, recipient),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """INSERT INTO match_apply (from_user_id, to_user_id, message, status, expire_at, created_at)
            VALUES (%s,%s,%s,0,%s,%s)""",
            (
                sender, recipient,
                "看到你的资料觉得很聊得来，想申请认识一下。",
                now + timedelta(hours=48), now - timedelta(hours=index + 2),
            ),
        )
        cur.execute(
            """INSERT INTO user_swipe_record (user_id, target_user_id, action, scene)
            VALUES (%s,%s,3,'recommend')
            ON DUPLICATE KEY UPDATE created_at=VALUES(created_at)""",
            (sender, recipient),
        )
        counts["applications"] += 1
    return counts


_FAVORITE_ACTIONS: tuple[tuple[str, str, int], ...] = (
    ("13800001001", "17870810291", 1),
    ("13800001002", "17870810286", 1),
    ("13998020600", "13800001011", 1),
    ("13905000870", "13800001014", 1),
    ("17870810285", "13800001015", 1),
    ("17870810286", "13800001001", 1),
    ("17870810291", "13998020600", 1),
    ("13800001011", "17870810285", 2),
    ("13800001012", "13998020600", 2),
    ("13800001013", "17870810291", 2),
    ("13800001014", "13905000870", 2),
    ("13800001015", "13800001002", 2),
    ("13800001016", "13800001001", 2),
    ("13800001001", "13800001012", 3),
    ("13800001002", "13905000870", 3),
    ("13998020600", "13800001013", 3),
    ("17870810286", "13800001011", 3),
    # 账号一：我的喜欢 / 我的关注 / 收到的喜欢 都要有内容。
    (PRIMARY_VIEWER_PHONE, "13800001001", 1),
    (PRIMARY_VIEWER_PHONE, "13800001011", 1),
    (PRIMARY_VIEWER_PHONE, "13800001001", 3),
    (PRIMARY_VIEWER_PHONE, "13998020600", 3),
    (PRIMARY_VIEWER_PHONE, "17870810291", 2),
    ("13800001001", PRIMARY_VIEWER_PHONE, 1),
    ("13998020600", PRIMARY_VIEWER_PHONE, 1),
    ("13800001011", PRIMARY_VIEWER_PHONE, 3),
)


def _seed_favorites_and_history(cur: Any, ids: dict[str, int], now: datetime) -> dict[str, int]:
    counts = {"favorites": 0, "browse": 0, "visitors": 0, "exposure": 0}
    for index, (actor_phone, target_phone, kind) in enumerate(_FAVORITE_ACTIONS):
        actor, target = ids[actor_phone], ids[target_phone]
        cur.execute(
            """INSERT IGNORE INTO user_favorite (user_id, target_user_id, type, created_at)
            VALUES (%s,%s,%s,%s)""",
            (actor, target, kind, now - timedelta(days=index + 1)),
        )
        counts["favorites"] += cur.rowcount
    phones = list(ids)
    for index, actor_phone in enumerate(phones):
        for offset in range(1, 4):
            target_phone = phones[(index + offset) % len(phones)]
            actor, target = ids[actor_phone], ids[target_phone]
            if actor == target:
                continue
            cur.execute(
                "SELECT id FROM user_browse_history WHERE user_id=%s AND target_user_id=%s LIMIT 1",
                (actor, target),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO user_browse_history (user_id, target_user_id, created_at)
                VALUES (%s,%s,%s)""",
                (actor, target, now - timedelta(hours=index * 3 + offset)),
            )
            counts["browse"] += 1
            counts["visitors"] += 1
    # 曝光记录：让发现页排序与「谁看过我」有真实数据支撑。
    package_id = _first_boost_package(cur)
    if package_id is None:
        return counts
    for index, phone in enumerate(phones):
        user_id = ids[phone]
        cur.execute(
            "SELECT id FROM user_exposure WHERE user_id=%s AND order_no=%s LIMIT 1",
            (user_id, f"DEMO-EXPOSE-{user_id}"),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """INSERT INTO user_exposure
            (user_id, package_id, order_no, start_at, end_at, status, created_at)
            VALUES (%s,%s,%s,%s,%s,1,%s)""",
            (
                user_id, package_id, f"DEMO-EXPOSE-{user_id}",
                now - timedelta(days=index), now + timedelta(days=7 - index), now - timedelta(days=index),
            ),
        )
        counts["exposure"] += 1
    return counts


_NOTIFICATION_SCRIPT: tuple[tuple[str, str, str, str, str], ...] = (
    ("like", "收到新的喜欢", "有人对你的资料表达了喜欢", "user", "like"),
    ("follow", "有人关注了你", "你收到了一条新的关注", "user", "follow"),
    ("comment", "动态收到新评论", "有人在你的动态下留言", "post", "comment"),
    ("match", "匹配成功", "你和对方互相喜欢，已建立匹配关系", "user", "match"),
    ("match_application", "收到新的认识申请", "有人申请认识你，可以去消息页处理", "user", "apply"),
    ("system", "资料完整度已达 100%", "你的资料已完整，可以开始浏览和申请认识了", "profile", "system"),
    ("activity", "活动报名成功", "你报名的线下活动已确认，请留意集合信息", "activity", "activity"),
)


def _seed_notifications(cur: Any, ids: dict[str, int], now: datetime) -> int:
    phones = list(ids)
    written = 0
    for index, phone in enumerate(phones):
        user_id = ids[phone]
        for order, (event, title, content, target_type, slug) in enumerate(_NOTIFICATION_SCRIPT):
            actor_phone = phones[(index + order + 1) % len(phones)]
            actor = ids[actor_phone]
            if actor == user_id:
                continue
            cur.execute(
                """SELECT id FROM user_notification
                WHERE user_id=%s AND notification_type=%s AND title=%s LIMIT 1""",
                (user_id, event, title),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO user_notification
                (user_id, notification_type, title, content, related_user_id, related_id,
                 is_read, created_at, target_type, target_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    user_id, event, title, content, actor, actor,
                    1 if order % 3 == 0 else 0,
                    now - timedelta(hours=index + order * 3), target_type, actor,
                ),
            )
            written += 1
    return written


def _seed_recommendations(cur: Any, ids: dict[str, int], now: datetime) -> int:
    """Precompute daily recommendations so the AI 推荐 / 每日推荐 surfaces have rows."""
    written = 0
    pairs = list(ids.items())
    for index, (phone, user_id) in enumerate(pairs):
        for offset in range(1, 4):
            other_phone, other_id = pairs[(index + offset) % len(pairs)]
            if other_id == user_id:
                continue
            cur.execute(
                """INSERT IGNORE INTO user_match_recommend
                (user_id, recommend_user_id, recommend_date, match_score, match_reason,
                 recommend_source, is_viewed)
                VALUES (%s,%s,CURDATE(),%s,%s,'system',%s)""",
                (
                    user_id, other_id, 78.0 + (index + offset) % 18,
                    "兴趣标签重合，且同城生活半径接近", 1 if offset == 1 else 0,
                ),
            )
            written += cur.rowcount
    return written


def _refresh_paper_planes(cur: Any, ids: dict[str, int], now: datetime) -> dict[str, int]:
    """Keep the paper-plane surface populated: extend expiry and top up to 3 per owner.

    纸飞机有 24 小时有效期，早期演示数据过期后「捡纸飞机」会返回空列表，
    因此这里续期已有记录，并保证每位演示会员都有可捡的纸飞机。
    """
    counts = {"refreshed": 0, "added": 0}
    cur.execute(
        """UPDATE paper_plane SET expire_at=%s, status=1, moderation_status=1
        WHERE user_id IN (%s) AND (expire_at IS NULL OR expire_at <= UTC_TIMESTAMP())"""
        % ("%s", ",".join(["%s"] * len(ids))),
        (now + timedelta(days=3), *ids.values()),
    )
    counts["refreshed"] = cur.rowcount
    drafts = (
        ("最近在学着把生活节奏放慢一点，希望认识同样愿意认真生活的人。", ("生活", "真诚")),
        ("周末准备去近郊走走，想找个可以慢慢聊天的同伴。", ("同城", "周末")),
        ("今天给自己做了一顿饭，觉得稳定的日常也值得记录。", ("日常", "记录")),
    )
    for phone, user_id in ids.items():
        cur.execute(
            "SELECT COUNT(*) c FROM paper_plane WHERE user_id=%s AND status=1 AND expire_at > UTC_TIMESTAMP()",
            (user_id,),
        )
        if int(cur.fetchone()["c"]) >= 3:
            continue
        for content, tags in drafts:
            cur.execute(
                "SELECT id FROM paper_plane WHERE user_id=%s AND content=%s LIMIT 1",
                (user_id, content),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE paper_plane SET expire_at=%s, status=1, moderation_status=1 WHERE id=%s",
                    (now + timedelta(days=3), int(existing["id"])),
                )
                continue
            cur.execute(
                """INSERT INTO paper_plane
                (user_id, content, images, city, tags, is_anonymous, reply_count, status,
                 moderation_status, expire_at, created_at)
                VALUES (%s,%s,%s,%s,%s,1,0,1,1,%s,%s)""",
                (
                    user_id, content, json.dumps([], ensure_ascii=False),
                    MEMBER_BY_PHONE[phone]["city"], json.dumps(list(tags), ensure_ascii=False),
                    now + timedelta(days=3), now - timedelta(hours=2),
                ),
            )
            counts["added"] += 1
    return counts


def _seed_community_extras(cur: Any, ids: dict[str, int], now: datetime) -> dict[str, int]:
    """Give every new member at least one post, so 社区六种流 都有内容。"""
    counts = {"posts": 0, "comments": 0}
    topics = {}
    cur.execute("SELECT id, name FROM community_topic WHERE is_active=1")
    for row in cur.fetchall():
        topics[str(row["name"])] = int(row["id"])
    if not topics:
        return counts
    topic_names = list(topics)
    snippets = (
        ("最近在练习把自己的想法说清楚，发现坦诚不等于急着给答案。", ""),
        ("周末去走了走老城区，慢慢认识一座城市，也慢慢认识自己。", "profile-woman-community.webp"),
        ("今天的练习是：说需求之前，先确认自己真正想要的是什么。", ""),
        ("下班后给自己做了一顿饭，照顾好自己是很重要的一课。", "profile-man-light.webp"),
        ("比起热闹的开场，我更珍惜可以把一件小事聊完整的下午。", "profile-woman-alt.webp"),
        ("想发起一次轻松的周末咖啡散步，不赶时间，也不预设结果。", ""),
        ("关系里有分歧很正常，重要的是我们愿不愿意继续把话说完。", "profile-man-sea.webp"),
        ("把周末留给阅读和朋友，生活不是等待谁出现之后才开始。", ""),
        ("认识一个人，可以从分享最近喜欢的一首歌开始。", "profile-woman-main.webp"),
        ("今天走了很远的路，回家时突然觉得，稳定的自己很有力量。", "profile-man-alt.webp"),
        ("认真生活这件事，本身就是一种吸引力。", ""),
        ("把想说的话写下来之后，发现自己其实已经想清楚了很多。", "profile-woman-community.webp"),
        ("和喜欢的人一起散步，是最近最想实现的小事。", "profile-man-light.webp"),
        ("最近开始记录每天的一件小事，坚持了二十天。", ""),
        ("希望遇到一个愿意一起把日子过细的人。", "profile-woman-main.webp"),
        ("周末做了一整天的饭，请朋友来家里吃了顿晚饭。", "profile-man-sea.webp"),
    )
    phones = [item["phone"] for item in MEMBERS]
    for index, phone in enumerate(phones):
        user_id = ids.get(phone)
        if user_id is None:
            continue
        content, image_name = snippets[index % len(snippets)]
        topic_name = topic_names[index % len(topic_names)]
        images = (
            json.dumps([f"/static/portraits/{image_name}"], ensure_ascii=False)
            if image_name else json.dumps([], ensure_ascii=False)
        )
        city = MEMBERS[index]["city"]
        cur.execute(
            "SELECT id FROM community_post WHERE user_id=%s AND content=%s LIMIT 1",
            (user_id, content),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """INSERT INTO community_post
            (user_id, topic_id, content, images, location, visibility, declaration, status,
             view_count, like_count, comment_count, created_at, updated_at, moderation_status)
            VALUES (%s,%s,%s,%s,%s,0,'',1,%s,0,0,%s,%s,1)""",
            (
                user_id, topics[topic_name], content, images, city,
                30 + index * 7, now - timedelta(hours=index + 1), now - timedelta(hours=index + 1),
            ),
        )
        post_id = int(cur.lastrowid)
        counts["posts"] += 1
        cur.execute(
            "INSERT IGNORE INTO community_topic_participant (topic_id, user_id) VALUES (%s,%s)",
            (topics[topic_name], user_id),
        )
        commenter_phone = phones[(index + 3) % len(phones)]
        commenter = ids.get(commenter_phone)
        if commenter and commenter != user_id:
            text = "这段话很有共鸣，谢谢分享。"
            cur.execute(
                """SELECT id FROM community_comment WHERE post_id=%s AND user_id=%s AND content=%s LIMIT 1""",
                (post_id, commenter, text),
            )
            if not cur.fetchone():
                cur.execute(
                    """INSERT INTO community_comment (post_id, user_id, content, status, created_at)
                    VALUES (%s,%s,%s,1,%s)""",
                    (post_id, commenter, text, now - timedelta(minutes=30 + index)),
                )
                counts["comments"] += 1
        for offset in (2, 5):
            liker = ids.get(phones[(index + offset) % len(phones)])
            if liker and liker != user_id:
                cur.execute(
                    "INSERT IGNORE INTO community_like (user_id, target_id, type) VALUES (%s,%s,1)",
                    (liker, post_id),
                )
        cur.execute(
            """UPDATE community_post SET
            like_count=(SELECT COUNT(*) FROM community_like WHERE target_id=%s AND type=1),
            comment_count=(SELECT COUNT(*) FROM community_comment WHERE post_id=%s AND status=1)
            WHERE id=%s""",
            (post_id, post_id, post_id),
        )
    return counts


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def seed_community_members(
    connection: Any = None,
    *,
    environment: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """填充完整会员数据；返回各表写入统计。

    步骤：素材生成 → 用户与资料 → 认证 → 媒体 → 完整度 → 权益 → 关系链 →
    社区补充 → 通知。全部在一条事务内提交，失败回滚。
    """
    _assert_environment(environment)
    owned = connection is None
    if owned:
        connection = _connect()
    moment = (now or datetime.now(UTC)).replace(tzinfo=None)
    cur = connection.cursor()
    stats: dict[str, Any] = {
        "created": [], "updated": [], "profiles": 0, "certifications": 0, "media": 0,
        "completion_100": 0, "memberships": 0, "extras": 0,
    }
    try:
        for kind, name, days, price, sort in _BOOST_PACKAGES:
            cur.execute(
                """INSERT INTO config_boost_package (type, name, duration_days, price, sort, is_active)
                SELECT %s,%s,%s,%s,%s,1
                WHERE NOT EXISTS (
                    SELECT 1 FROM config_boost_package WHERE type=%s AND name=%s)""",
                (kind, name, days, price, sort, kind, name),
            )
        cur.execute("SELECT id FROM config_boost_package WHERE type=2 AND is_active=1 ORDER BY id LIMIT 1")
        boost_row = cur.fetchone()
        boost_package_id = int(boost_row["id"]) if boost_row else None
        for order, (name, description) in enumerate(_EXTRA_CERT_TYPES):
            cur.execute(
                """INSERT INTO config_auth_type (name, description, require_realname, sort, status)
                VALUES (%s,%s,1,%s,1)
                ON DUPLICATE KEY UPDATE description=VALUES(description), status=1""",
                (name, description, len(_EXTRA_CERT_TYPES) - order),
            )

        ids: dict[str, int] = {}
        for index, member in enumerate(MEMBERS, start=1):
            # 素材落在 storage/uploads/<user_id>/ 下，因此先确定 user_id 再渲染。
            user_id, created = _ensure_user(cur, member, moment)
            assets = _prepare_assets(member, user_id)
            if created:
                stats["created"].append(member["phone"])
            else:
                stats["updated"].append(member["phone"])
            ids[member["phone"]] = user_id
            cur.execute(
                "UPDATE users SET avatar=%s WHERE id=%s", (assets["avatar"]["url"], user_id)
            )
            _upsert_profile(cur, member, user_id, assets, moment)
            _upsert_preference(cur, member, user_id)
            _upsert_privacy(cur, user_id)
            _upsert_auth(cur, member, user_id, assets["certificates"])
            _upsert_commitment(cur, member, user_id, assets["certificates"]["pledge"]["url"], moment)
            _replace_media(cur, member, user_id, assets)
            _upsert_role_intent_agreements(cur, member, user_id, moment)
            _upsert_points(cur, user_id, moment, index)
            _upsert_membership_wallet(cur, member, user_id, moment, boost_package_id)
            _upsert_quota(cur, user_id, moment)
            _upsert_profile_extras(cur, member, user_id)
            _upsert_other_certifications(cur, member, user_id, assets["certificates"])
            score = _refresh_completion(cur, user_id)
            stats["profiles"] += 1
            stats["certifications"] += 1
            stats["media"] += 2 + len(assets["album"])
            if score >= 100:
                stats["completion_100"] += 1
            if member.get("membership"):
                stats["memberships"] += 1

        relations = _seed_relations(cur, ids, moment)
        favorites = _seed_favorites_and_history(cur, ids, moment)
        notifications = _seed_notifications(cur, ids, moment)
        recommendations = _seed_recommendations(cur, ids, moment)
        community = _seed_community_extras(cur, ids, moment)
        planes = _refresh_paper_planes(cur, ids, moment)
        connection.commit()
        stats.update(relations)
        stats.update(favorites)
        stats.update(community)
        stats["paper_planes"] = planes
        stats["notifications"] = notifications
        stats["recommendations"] = recommendations
        return stats
    except Exception:
        connection.rollback()
        raise
    finally:
        cur.close()
        if owned:
            connection.close()


def _first_boost_package(cur: Any) -> int | None:
    cur.execute("SELECT id FROM config_boost_package WHERE type=2 AND is_active=1 ORDER BY id LIMIT 1")
    row = cur.fetchone()
    return int(row["id"]) if row else None


if __name__ == "__main__":
    result = seed_community_members()
    print("会员数据已写入: " + json.dumps(result, ensure_ascii=False, default=str))
