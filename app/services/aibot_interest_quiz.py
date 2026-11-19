"""恋 AI BOT 兴趣连问：第 2 轮起按已有标签动态出题。

提示词权威：项目文档 10 §5.3；对照 19 / 27。
改本题干策略时请同步文档并 bump INTEREST_QUIZ_PROMPT_VERSION。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException

from app.services.ai.flags import AiFeature
from app.services.ai_provider import complete, parse_json
from app.services.aibot_prompts import get_agent_meta

logger = logging.getLogger(__name__)

# 与项目文档 10 §5.3 / 27 对齐；审计与抽检用。
INTEREST_QUIZ_PROMPT_VERSION = "interest-quiz-v2-2026-11-19"

_FIXED_STEMS = {
    "平时更爱吃什么",
    "空下来更爱玩什么",
    "旅行更想去哪一类",
    "最近更想看什么书",
    "影视动漫更偏哪边",
    "运动上更喜欢什么",
    "听音乐更爱哪一类",
    "周末更想怎么过",
}

_INTEREST_QUIZ_SYSTEM = (
    "你是兴趣连问出题助手，服务婚恋平台「宣誓爱」的恋 AI BOT。"
    "只输出一个 JSON 对象，不要 Markdown，不要题解旁白。"
    "目标：按用户已有兴趣标签扩维出题，让画像变厚，而不是复读第 1 轮固定问卷。"
)

# 按类目的扩展题库（禁止复读第 1 轮固定题原文）
_EXPAND_BANK: list[dict[str, Any]] = [
    {
        "q": "吃这块还想再细一点：更偏辣还是清淡？",
        "category": "生活",
        "opts": ["重口麻辣", "微辣刚好", "清淡鲜香", "甜口优先", "看心情"],
        "anchors": ["火锅", "美食", "日料", "甜品", "烧烤"],
    },
    {
        "q": "约饭时你更在意哪一点？",
        "category": "生活",
        "opts": ["味道正宗", "环境舒服", "方便好约", "性价比", "新店尝鲜"],
        "anchors": ["火锅", "美食", "咖啡", "探店"],
    },
    {
        "q": "玩手游/开黑时你更像哪种人？",
        "category": "游戏",
        "opts": ["带飞输出", "稳稳辅助", "休闲随便玩", "更爱单机", "不常开黑"],
        "anchors": ["MOBA", "手游", "开黑", "单机游戏", "游戏"],
    },
    {
        "q": "如果一起出门玩，你更想怎么玩？",
        "category": "游戏",
        "opts": ["剧本杀密室", "桌游聚会", "户外走走", "宅家刷剧", "看展拍照"],
        "anchors": ["桌游", "剧本杀", "短视频"],
    },
    {
        "q": "旅行落地后，你第一天更想做什么？",
        "category": "旅行",
        "opts": ["先睡回血", "冲热门打卡", "随便逛逛", "找当地吃的", "跟着感觉走"],
        "anchors": ["旅行", "citywalk", "海岛", "露营", "徒步"],
    },
    {
        "q": "下次短途出游，你更倾向哪类节奏？",
        "category": "旅行",
        "opts": ["慢悠悠放空", "行程排满", "美食为主", "自然风光", "人文古迹"],
        "anchors": ["旅行", "citywalk", "海岛度假"],
    },
    {
        "q": "追剧/看电影时你更在意什么？",
        "category": "动漫影视",
        "opts": ["剧情上头", "人设好看", "画面音乐", "轻松解压", "豆瓣口碑"],
        "anchors": ["剧集综", "电影院", "番剧", "看剧"],
    },
    {
        "q": "动漫影视这块，最近更想补哪一类？",
        "category": "动漫影视",
        "opts": ["热血番", "治愈日常", "悬疑烧脑", "爱情甜宠", "纪录现实"],
        "anchors": ["番剧", "剧集综", "电影院"],
    },
    {
        "q": "运动这块，你更想找什么样的搭子？",
        "category": "运动",
        "opts": ["一起跑步", "球类约战", "拉伸放松", "游泳消暑", "先从散步起"],
        "anchors": ["跑步", "篮球", "健身", "瑜伽", "游泳", "羽毛球"],
    },
    {
        "q": "听歌时你更常处在什么场景？",
        "category": "音乐",
        "opts": ["通勤路上", "睡前放空", "运动提劲", "工作时底噪", "现场气氛"],
        "anchors": ["流行歌", "说唱", "乐队现场", "音乐"],
    },
    {
        "q": "看书时你更喜欢怎样的节奏？",
        "category": "阅读",
        "opts": ["一口气啃完", "碎片化读", "听书为主", "边读边聊", "最近读得少"],
        "anchors": ["小说", "非虚构", "漫画", "阅读"],
    },
    {
        "q": "周末如果多半天空档，你优先补哪件事？",
        "category": "生活",
        "opts": ["补觉躺平", "约饭逛街", "户外活动", "看展听演", "整理家务"],
        "anchors": ["咖啡", "摄影", "看展", "周末"],
    },
    {
        "q": "社交上你更舒服的相处密度是？",
        "category": "情感社交",
        "opts": ["常联系短聊", "约得出门", "深度长聊", "保持边界", "看缘分"],
        "anchors": ["桌游", "剧本杀", "聚会"],
    },
    {
        "q": "咖啡店里你更常点哪一类？",
        "category": "生活",
        "opts": ["美式提神", "拿铁奶香", "手冲细品", "甜品搭配", "不太喝"],
        "anchors": ["咖啡", "探店", "甜品"],
    },
    {
        "q": "拍照分享时你更在意什么？",
        "category": "艺术",
        "opts": ["构图光影", "记录日常", "修得很美", "随手一拍", "不太发"],
        "anchors": ["摄影", "看展", "短视频"],
    },
    {
        "q": "露营或户外过夜，你更能接受？",
        "category": "旅行",
        "opts": ["精致营地", "轻装徒步", "只白天户外", "更爱酒店", "还没试过"],
        "anchors": ["露营", "徒步", "旅行"],
    },
    {
        "q": "球类运动里你更想碰哪样？",
        "category": "运动",
        "opts": ["羽毛球", "篮球", "网球", "乒乓球", "先看再说"],
        "anchors": ["羽毛球", "篮球", "网球", "运动"],
    },
    {
        "q": "听现场演出你更冲哪类？",
        "category": "音乐",
        "opts": ["乐队 Live", "流行演唱会", "小型音乐会", "更爱耳机", "看搭子"],
        "anchors": ["乐队现场", "流行歌", "音乐"],
    },
    {
        "q": "漫画/小说你更吃哪一口？",
        "category": "阅读",
        "opts": ["爱情甜宠", "悬疑烧脑", "热血成长", "现实题材", "最近很少看"],
        "anchors": ["漫画", "小说", "阅读"],
    },
]


def _norm_stem(text: str) -> str:
    s = re.sub(r"[\s\?？!！。、，,：:·\-—_/\\（）()【】\[\]]+", "", str(text or ""))
    return s[:24]


def _interests_line(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for it in items[:8]:
        tag = str(it.get("tag") or "").strip()
        if not tag:
            continue
        heat = it.get("heat")
        level = str(it.get("level") or "").strip()
        try:
            heat_s = f"{float(heat):.2f}"
        except Exception:
            heat_s = "?"
        parts.append(f"{tag}({heat_s}·{level or '了解'})")
    return "、".join(parts) if parts else "（暂无）"


def _asked_line(asked: list[str]) -> str:
    cleaned = [str(x).strip() for x in asked if str(x).strip()]
    return " / ".join(cleaned[:20]) if cleaned else "（无）"


def _rule_fallback(
    interests: list[dict[str, Any]],
    asked_questions: list[str],
    *,
    max_n: int = 4,
) -> list[dict[str, Any]]:
    asked_norms = {_norm_stem(x) for x in asked_questions}
    for stem in _FIXED_STEMS:
        asked_norms.add(_norm_stem(stem))

    tags = [str(it.get("tag") or "").strip() for it in interests if str(it.get("tag") or "").strip()]
    cats = [str(it.get("category") or "").strip() for it in interests if str(it.get("category") or "").strip()]
    tag_set = set(tags)

    scored: list[tuple[int, dict[str, Any]]] = []
    for item in _EXPAND_BANK:
        q = str(item["q"])
        if _norm_stem(q) in asked_norms:
            continue
        score = 0
        for a in item.get("anchors") or []:
            if a in tag_set:
                score += 3
            for t in tags:
                if a in t or t in a:
                    score += 2
        cat = str(item.get("category") or "")
        if cat and cat in cats:
            score += 1
        scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], x[1]["q"]))
    picked = [x[1] for x in scored if x[0] > 0][:max_n]
    if len(picked) < 2:
        for _score, item in scored:
            if item in picked:
                continue
            picked.append(item)
            if len(picked) >= min(3, max_n):
                break

    out: list[dict[str, Any]] = []
    for item in picked[:max_n]:
        out.append(
            {
                "q": str(item["q"])[:24],
                "category": str(item.get("category") or "生活"),
                "opts": [str(o)[:12] for o in (item.get("opts") or [])][:6],
                "anchor_tags": list(item.get("anchors") or [])[:4],
            }
        )
    return out


def _normalize_questions(raw: Any, *, min_n: int = 2, max_n: int = 6) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or "").strip()
        if not q or _norm_stem(q) in seen:
            continue
        if any(_norm_stem(q).find(_norm_stem(s)) >= 0 or _norm_stem(s).find(_norm_stem(q)) >= 0 for s in _FIXED_STEMS):
            continue
        opts_raw = item.get("opts") if isinstance(item.get("opts"), list) else []
        opts = [str(o).strip()[:12] for o in opts_raw if str(o).strip()]
        if len(opts) < 3:
            continue
        cat = str(item.get("category") or item.get("cat") or "生活").strip()[:16] or "生活"
        anchors = item.get("anchor_tags") if isinstance(item.get("anchor_tags"), list) else []
        out.append(
            {
                "q": q[:24],
                "category": cat,
                "opts": opts[:6],
                "anchor_tags": [str(a).strip()[:8] for a in anchors if str(a).strip()][:4],
            }
        )
        seen.add(_norm_stem(q))
        if len(out) >= max_n:
            break
    return out if len(out) >= min_n else []


def build_interest_quiz_messages(
    *,
    agent_id: str,
    session_count: int,
    interests: list[dict[str, Any]],
    asked_questions: list[str],
    willingness: str = "",
) -> list[dict[str, str]]:
    """构建兴趣出题 messages；正文对齐项目文档 10 §5.3。"""
    meta = get_agent_meta(agent_id)
    next_no = max(2, int(session_count) + 1)
    fixed_line = " / ".join(sorted(_FIXED_STEMS))
    user = (
        "INTEREST_QUIZ\n"
        f"prompt_version={INTEREST_QUIZ_PROMPT_VERSION}\n"
        f"你是【{meta['name']}】。请为「这一位用户」策划下一轮兴趣选择题。\n"
        "第 1 轮固定题已问过，禁止再出同义题或换皮题。\n\n"
        f"【第1轮固定题干·禁止同义】{fixed_line}\n"
        f"【你的人设】{meta['persona']}\n"
        f"【本轮是用户的第 {next_no} 轮连问】\n"
        f"【已有兴趣标签】{json.dumps(interests[:20], ensure_ascii=False)}\n"
        f"【标签热度 Top】{_interests_line(interests)}\n"
        f"【近两轮已问过的题干】{_asked_line(asked_questions)}\n"
        f"【愿填程度信号】{willingness or '未知'}\n\n"
        "【你来决定】\n"
        "1. 本轮聊什么方向（主题自定，不按「第 N 轮必须某类」脚本）。\n"
        "2. 出几道题（2～6 道）：愿填弱→2～3；中→3～4；强→4～6。\n"
        "3. why_count 用一句话说明题量理由（给日志）。\n"
        "4. session_theme 用口语短标题，像红娘开口。\n\n"
        "【出题策略】\n"
        "A. 有 Top 标签时优先同族扩维（看剧→类型/追更；火锅→辣度/约饭）。\n"
        "B. 标签空或同族已挖透时再开新维"
        "（生活|美食|游戏|旅行|阅读|动漫影视|运动|音乐|情感社交|艺术|其他）。\n"
        "C. 同一轮尽量覆盖 ≥2 个小角度，避免全问同一细点。\n"
        "D. 每题选项 4～6 个；宜能落成 ≤8 字标签；可含 1 个低负担项，但不要题题敷衍。\n"
        "E. 不同用户因标签不同题目应不同；禁止一套万能卷。\n"
        "F. 题干短、可点、不审判；可轻微带人设，不要废话寒暄。\n\n"
        "【硬禁止】\n"
        "1. 禁止与已问题干同义/换皮；禁止复读第 1 轮固定 8 题。\n"
        "2. 禁止盘问收入、资产、精确住址、联系方式；无歧视、无性暗示、无未成年人相关。\n"
        "3. 禁止输出解析或旁白；只要题。\n"
        "4. anchor_tags 只能来自已有标签或本题选项可落成的短标签，禁止虚构。\n\n"
        "【输出 JSON】\n"
        '{"session_theme":"≤12字","question_count":4,"why_count":"≤20字",'
        '"questions":[{"q":"题干≤24字","category":"生活|美食|游戏|旅行|阅读|动漫影视|运动|音乐|情感社交|艺术|其他",'
        '"opts":["A","B","C","D"],"anchor_tags":["看剧"]}]}'
    )
    return [
        {"role": "system", "content": _INTEREST_QUIZ_SYSTEM},
        {"role": "user", "content": user},
    ]


async def generate_interest_quiz(
    *,
    agent_id: str = "anian",
    session_count: int = 1,
    interests: list[dict[str, Any]] | None = None,
    asked_questions: list[str] | None = None,
    willingness: str = "",
) -> dict[str, Any]:
    items = [x for x in (interests or []) if isinstance(x, dict)]
    asked = [str(x).strip() for x in (asked_questions or []) if str(x).strip()]
    meta = get_agent_meta(agent_id or "anian")

    source = "ai"
    theme = "兴趣再挖深一点"
    why = "按已有爱好扩维"
    questions: list[dict[str, Any]] = []

    messages = build_interest_quiz_messages(
        agent_id=meta["id"],
        session_count=session_count,
        interests=items,
        asked_questions=asked,
        willingness=willingness,
    )
    try:
        raw = await complete(
            messages,
            json_mode=True,
            scene="aibot_interest_quiz",
            feature=AiFeature.ADVISOR,
        )
        parsed = parse_json(raw)
        questions = _normalize_questions(parsed.get("questions"))
        theme = str(parsed.get("session_theme") or theme).strip()[:12] or theme
        why = str(parsed.get("why_count") or why).strip()[:20] or why
        if not questions:
            raise ValueError("empty questions")
    except Exception as exc:
        if isinstance(exc, HTTPException) and int(getattr(exc, "status_code", 0) or 0) not in (
            503,
            502,
            500,
        ):
            raise
        logger.warning(
            "aibot interest quiz fallback agent=%s err=%s",
            meta["id"],
            type(exc).__name__,
            exc_info=True,
        )
        source = "rule"
        questions = _rule_fallback(items, asked, max_n=4)
        theme = "顺着你的爱好再问几题"
        why = "模型不可用，本地扩维兜底"

    if not questions:
        questions = _rule_fallback(items, asked, max_n=3)

    return {
        "session_theme": theme,
        "question_count": len(questions),
        "why_count": why,
        "source": source,
        "questions": questions,
        "agent_id": meta["id"],
        "agent_name": meta["name"],
        "prompt_version": INTEREST_QUIZ_PROMPT_VERSION,
    }
