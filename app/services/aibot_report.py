"""恋 AI BOT 恋爱报告：会员资料 + AI 画像 + 聊天情绪 → 可回看的多版本报告。

三条数据来源缺一不可，且必须标明出处，不允许模型把没提供的事实编出来：

1. **会员资料**（``users`` / ``user_profile`` / ``bot_intake_json``）：精准值优先，
   BOT 采集的区间/选项回落，沿用 ``aibot_profile_sync.facts_for_ai`` 的一套口径。
2. **AI 画像**（``ai_profile_revision*``）：只取最近一次已发布 revision 的条目，
   个人画像与理想型分开取，不改写、不合并成结论。
3. **聊天情绪**（``aibot_turn``）：最近 N 轮真实对话摘录交给模型判断情绪与节奏。

分数（竞争力 / 恋爱指数 / 分项条）由客户端既有打分表计算并随请求传入，
本服务只落库和引用，不让模型编数字——同一份资料在任何端都应得到同一个分数。
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis import consume_daily, daily_quota_key, refund_daily
from app.schemas.ai_profile import PROFILE_ENTRY_CATEGORY_LABELS
from app.services.aibot_prompts import get_agent_meta
from app.services.ai.flags import AiFeature
from app.services.ai_provider import complete, parse_json

logger = logging.getLogger(__name__)

_REPORT_QUOTA_PREFIX = "aibot-report"
# 生成次数限制（查看历史不受限）；报告要陪用户走完整个择偶周期，
# 一天 6 次足够覆盖「补充资料后重算」，又挡得住脚本刷模型。
_REPORT_DAILY_LIMIT = 6
_CHAT_DIGEST_LIMIT = 40

# 情绪兜底判词：模型不可用时也不能让报告缺这一块。
_NEGATIVE_WORDS = (
    "累", "烦", "焦虑", "压力", "难受", "失望", "害怕", "怕", "不想", "算了吧",
    "没意思", "迷茫", "孤独", "委屈", "哭", "分手", "前任",
)
_POSITIVE_WORDS = (
    "期待", "开心", "想认识", "喜欢", "试试", "有希望", "加油", "愿意", "见面",
    "聊得来", "心动", "准备好了",
)


def _now_iso() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _report_date() -> str:
    now = datetime.now()
    return f"{now.year} 年 {now.month} 月"


def _clean_list(value: Any, limit: int = 6) -> list[str]:
    """模型给出的列表字段一律清洗成短句数组，拒绝嵌套与空串。"""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    out: list[str] = []
    for raw in items:
        if isinstance(raw, (dict, list, tuple)):
            continue
        s = str(raw or "").strip()
        if not s:
            continue
        if s not in out:
            out.append(s[:120])
        if len(out) >= limit:
            break
    return out


def _clamp_pct(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except Exception:
        return 0
    return max(0, min(100, n))


def _normalize_bars(raw: Any) -> list[dict[str, Any]]:
    """客户端分项条只接受 {name, pct}，顺序按 pct 降序以稳定展示。"""
    if not isinstance(raw, (list, tuple)):
        return []
    bars: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        bars.append({"name": name[:16], "pct": _clamp_pct(item.get("pct"))})
    bars.sort(key=lambda b: b["pct"], reverse=True)
    return bars


async def _load_member_facts(db: AsyncSession, user_id: int) -> dict[str, Any]:
    from app.services.aibot_chat import _load_member_facts as load_facts

    return await load_facts(db, user_id)


async def _load_nickname(db: AsyncSession, user_id: int) -> str:
    from app.services.aibot_chat import _load_nickname as load_nickname

    return await load_nickname(db, user_id)


async def _load_ai_profile_lines(db: AsyncSession, user_id: int) -> list[str]:
    """取最近一次已发布 AI 画像条目，按「主体｜分类：内容」折成文本行。"""
    lines: list[str] = []
    for subject, subject_label in (("personal", "我的画像"), ("ideal_partner", "理想型")):
        try:
            latest = (
                await db.execute(
                    text(
                        """SELECT id FROM ai_profile_revision
                           WHERE user_id = :uid AND subject = :subject
                           ORDER BY revision_no DESC, id DESC LIMIT 1"""
                    ),
                    {"uid": user_id, "subject": subject},
                )
            ).mappings().first()
            if not latest:
                continue
            rows = (
                await db.execute(
                    text(
                        """SELECT field_key, category, content
                           FROM ai_profile_revision_field
                           WHERE revision_id = :rid AND field_kind = 'entry'
                           ORDER BY id"""
                    ),
                    {"rid": int(latest["id"])},
                )
            ).mappings().all()
        except Exception:
            logger.debug("aibot report ai profile lookup skipped", exc_info=True)
            continue
        for row in rows:
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            category = str(row.get("category") or "")
            label = PROFILE_ENTRY_CATEGORY_LABELS.get(category, category or "描述")
            lines.append(f"{subject_label}｜{label}：{content[:160]}")
    return lines


async def _load_chat_digest(db: AsyncSession, user_id: int) -> tuple[list[str], int]:
    """最近一轮会话里的真实对话摘录（用户 + 红娘），供模型判断情绪。"""
    try:
        session_row = (
            await db.execute(
                text(
                    """SELECT session_id FROM aibot_session
                       WHERE user_id = :uid ORDER BY updated_at DESC LIMIT 1"""
                ),
                {"uid": user_id},
            )
        ).mappings().first()
        if not session_row:
            return [], 0
        rows = (
            await db.execute(
                text(
                    """SELECT role, content FROM aibot_turn
                       WHERE session_id = :sid AND role IN ('user', 'assistant')
                       ORDER BY id DESC LIMIT :lim"""
                ),
                {"sid": str(session_row["session_id"]), "lim": _CHAT_DIGEST_LIMIT},
            )
        ).mappings().all()
    except Exception:
        logger.debug("aibot report chat lookup skipped", exc_info=True)
        return [], 0
    lines: list[str] = []
    for row in reversed(list(rows)):
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        who = "用户" if str(row.get("role")) == "user" else "红娘"
        lines.append(f"{who}：{content[:160]}")
    user_turns = sum(1 for line in lines if line.startswith("用户："))
    return lines, user_turns


def _rule_emotion(chat_lines: list[str]) -> dict[str, Any]:
    """无模型时的情绪兜底：按用户话语里的情绪词计数，给出保守结论。"""
    user_text = "\n".join(line for line in chat_lines if line.startswith("用户："))
    if not user_text.strip():
        return {
            "label": "待观察",
            "score": 0.0,
            "trend": "平",
            "evidence": ["还没有足够的对话，情绪判断按资料与画像给出"],
            "advice": "多聊几句，红娘才能把情绪变化写进下一版报告。",
        }
    neg = sum(1 for w in _NEGATIVE_WORDS if w in user_text)
    pos = sum(1 for w in _POSITIVE_WORDS if w in user_text)
    diff = pos - neg
    if diff > 0:
        label, trend = "平稳偏好", "升"
    elif diff < 0:
        label, trend = "压力偏高", "降"
    else:
        label, trend = "平稳", "平"
    evidence: list[str] = []
    if neg:
        evidence.append(f"对话里出现 {neg} 类负向表达（累 / 焦虑 / 不想 等）")
    if pos:
        evidence.append(f"对话里出现 {pos} 类正向表达（期待 / 想认识 / 见面 等）")
    if not evidence:
        evidence.append("对话以日常信息为主，未出现明显情绪波动")
    return {
        "label": label,
        "score": round(max(-1.0, min(1.0, diff / 4.0)), 2),
        "trend": trend,
        "evidence": evidence,
        "advice": "先用轻量话题恢复节奏，再谈条件。" if diff < 0 else "节奏可以往前走一小步，先定一个可见面的动作。",
    }


def _rule_narrative(
    *,
    nickname: str,
    title: str,
    member_facts: dict[str, Any],
    ai_lines: list[str],
    emotion: dict[str, Any],
    love_index_label: str,
) -> dict[str, Any]:
    """模型不可用时的完整规则版正文，保证报告永远打得开。"""
    city = str(member_facts.get("city") or "所在城市")
    edu = member_facts.get("education_level") or member_facts.get("education_bot")
    job = member_facts.get("occupation")
    profile_bits: list[str] = []
    if edu is not None:
        profile_bits.append(f"学历：{edu}")
    if job:
        profile_bits.append(f"职业：{job}")
    profile_line = "；".join(profile_bits) if profile_bits else "资料还在补充中"
    ai_note = ai_lines[0] if ai_lines else "还没有已发布的 AI 画像，建议先做一次画像建构"
    return {
        "letter": (
            f"{nickname}{title}你好。这版报告由红娘按你的资料、"
            f"{'已发布的 AI 画像' if ai_lines else '当前已采集的信息'}与最近对话整理"
            f"（{profile_line}）。我们先看清优势与节奏，再谈具体的人。"
        ),
        "emotion": emotion,
        "part01": {
            "strengths": [profile_line, ai_note],
            "summary": f"当前恋爱意愿「{love_index_label}」，推荐节奏据此调节。",
        },
        "part02": {
            "objective": [f"主要生活在{city}，可接触的适婚圈层相对固定"],
            "subjective": ["择偶标准需要与真实供给对齐", "表达节奏比条件清单更影响推进"],
            "conclusion": "先把条件分成底线与加分两组，再进入推荐，能显著减少无效沟通。",
        },
        "part03": {
            "history": ["过往经历待你在采集里补充，越具体判断越准"],
            "pattern": ["先观察后投入", "对节奏与安全感敏感"],
            "growth": ["关键节点主动一小步", "把感受说清楚，而不是猜"],
        },
        "part04": {
            "current": [],
            "must": ["性别与年龄区间匹配", "结婚意愿明确"],
            "plus": ["同城", "共同兴趣", "情绪稳定"],
            "flexible": ["收入数字", "户籍细节"],
        },
        "part05": {
            "timeline": ["确认择偶三档", "轻聊破冰 → 见面", "用反馈驱动下一轮筛选"],
            "growth": ["见面复盘", "沟通与边界", "节点推动"],
        },
        "part06": {
            "issues": [
                {"name": "情绪节奏", "level": "中", "note": str(emotion.get("label") or "待观察")},
                {"name": "标准匹配度", "level": "中", "note": "用三档画像执行"},
            ],
            "tactics": ["按恋爱指数调节推荐量", "底线坚定、加分灵活"],
        },
        "part07": {
            "path": ["确认择偶方向", "开启推荐并见人", "用真实反馈修正标准"],
            "closing": "红娘会陪你走完整个过程，节奏由你定。",
        },
        "match_focus": ["同城优先", "意愿一致", "聊得来"],
    }


def build_report_messages(
    *,
    agent_id: str,
    nickname: str,
    member_facts: dict[str, Any],
    ai_lines: list[str],
    chat_lines: list[str],
    score_total: Any,
    love_index_label: str,
) -> list[dict[str, str]]:
    """拼报告提示词。纯函数，便于单测固定口径。"""
    meta = get_agent_meta(agent_id)
    facts_lines: list[str] = []
    for key, value in (member_facts or {}).items():
        if key.endswith("_source") or value is None or str(value).strip() == "":
            continue
        facts_lines.append(f"- {key}: {value}")
    facts_block = "\n".join(facts_lines) or "- （暂无精准资料，只有 BOT 采集）"
    ai_block = "\n".join(f"- {line}" for line in ai_lines) or "- （还没有已发布的 AI 画像）"
    chat_block = "\n".join(chat_lines) or "- （还没有对话，情绪写「待观察」，不要臆测）"

    system = (
        f"你是恋 AI BOT 的专属红娘【{meta['name']}】，现在要为会员「{nickname}」写一份婚恋分析报告。\n"
        "报告要给本人看，也要能被下载保存，因此必须只依据下面三块真实材料，"
        "不得编造用户没有提供的经历、收入、家庭细节；材料不足就写「待补充」。\n"
        "平台红线：不承诺关系结果，不制造年龄焦虑，不使用「命中注定」「最后机会」这类表述，"
        "不做心理诊断，不评价外貌高低。\n"
        "只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释性文字。\n"
        "除 emotion 的判断外，其它列表字段每项都是 20~60 字的中文短句，"
        "objective/subjective/history/pattern/growth/timeline/must/plus/flexible 每项 2~4 条。"
    )
    user = (
        f"【会员资料（精准优先，bot 来源可能是区间，不能当精确值）】\n{facts_block}\n\n"
        f"【已发布的 AI 画像】\n{ai_block}\n\n"
        f"【最近对话摘录（用于判断情绪，不要复述原文）】\n{chat_block}\n\n"
        f"【系统已算好的分数，不得改写】竞争力 {score_total} 分；恋爱意愿「{love_index_label}」。\n\n"
        "请输出 JSON，字段如下（缺失也保留键）：\n"
        "{\n"
        '  "letter": "顾问寄语，120~200 字，第二人称，先肯定再指出一个卡点",\n'
        '  "emotion": {\n'
        '    "label": "情绪标签，4~6 字，如 平稳期待 / 压力偏高 / 观望犹豫",\n'
        '    "score": -1.0 到 1.0 的数字（负=低压，正=积极）,\n'
        '    "trend": "升 | 平 | 降",\n'
        '    "evidence": ["对话里能支撑这个判断的现象，2~3 条，不要引用原句"],\n'
        '    "advice": "针对这个情绪的一条具体建议"\n'
        "  },\n"
        '  "part01": {"strengths": ["优势 2~3 条"], "summary": "一句话总结当前条件与意愿"},\n'
        '  "part02": {"objective": ["客观原因"], "subjective": ["主观原因"], "conclusion": "结论"},\n'
        '  "part03": {"history": ["情感经历要点"], "pattern": ["相处模式"], "growth": ["可成长点"]},\n'
        '  "part04": {\n'
        '    "current": [{"item": "期望项", "raw": "用户当前写法", "advice": "建议", "tier": "核心底线|可调整项|加分项"}],\n'
        '    "must": ["底线"], "plus": ["加分项"], "flexible": ["可让步项"]\n'
        "  },\n"
        '  "part05": {"timeline": ["时间节奏"], "growth": ["能力建设"]},\n'
        '  "part06": {"issues": [{"name": "议题", "level": "高|中|低", "note": "说明"}], "tactics": ["策略"]},\n'
        '  "part07": {"path": ["下一步动作"], "closing": "结尾一段话，不承诺结果"},\n'
        '  "match_focus": ["下一轮推荐该优先看什么，3 条"]\n'
        "}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _normalize_narrative(parsed: dict[str, Any]) -> dict[str, Any]:
    """把模型输出收敛成固定结构；任何缺失都退回空列表而不是报错。"""
    emotion_raw = parsed.get("emotion") if isinstance(parsed.get("emotion"), dict) else {}
    try:
        emotion_score = float(emotion_raw.get("score", 0) or 0)
    except Exception:
        emotion_score = 0.0
    trend = str(emotion_raw.get("trend") or "平").strip()
    if trend not in ("升", "平", "降"):
        trend = "平"
    emotion = {
        "label": str(emotion_raw.get("label") or "待观察").strip()[:16],
        "score": round(max(-1.0, min(1.0, emotion_score)), 2),
        "trend": trend,
        "evidence": _clean_list(emotion_raw.get("evidence"), 4),
        "advice": str(emotion_raw.get("advice") or "").strip()[:160],
    }

    def block(key: str) -> dict[str, Any]:
        value = parsed.get(key)
        return value if isinstance(value, dict) else {}

    part01 = block("part01")
    part02 = block("part02")
    part03 = block("part03")
    part04 = block("part04")
    part05 = block("part05")
    part06 = block("part06")
    part07 = block("part07")

    current: list[dict[str, str]] = []
    raw_current = part04.get("current")
    if isinstance(raw_current, (list, tuple)):
        for item in raw_current[:6]:
            if not isinstance(item, dict):
                continue
            item_name = str(item.get("item") or "").strip()
            if not item_name:
                continue
            tier = str(item.get("tier") or "可调整项").strip()
            if tier not in ("核心底线", "可调整项", "加分项"):
                tier = "可调整项"
            current.append(
                {
                    "item": item_name[:24],
                    "raw": str(item.get("raw") or "未填").strip()[:40],
                    "advice": str(item.get("advice") or "").strip()[:80],
                    "tier": tier,
                }
            )

    issues: list[dict[str, str]] = []
    raw_issues = part06.get("issues")
    if isinstance(raw_issues, (list, tuple)):
        for item in raw_issues[:5]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            level = str(item.get("level") or "中").strip()
            if level not in ("高", "中", "低"):
                level = "中"
            issues.append(
                {
                    "name": name[:24],
                    "level": level,
                    "note": str(item.get("note") or "").strip()[:120],
                }
            )

    return {
        "letter": str(parsed.get("letter") or "").strip()[:600],
        "emotion": emotion,
        "part01": {
            "strengths": _clean_list(part01.get("strengths"), 4),
            "summary": str(part01.get("summary") or "").strip()[:200],
        },
        "part02": {
            "objective": _clean_list(part02.get("objective"), 4),
            "subjective": _clean_list(part02.get("subjective"), 4),
            "conclusion": str(part02.get("conclusion") or "").strip()[:200],
        },
        "part03": {
            "history": _clean_list(part03.get("history"), 4),
            "pattern": _clean_list(part03.get("pattern"), 4),
            "growth": _clean_list(part03.get("growth"), 4),
        },
        "part04": {
            "current": current,
            "must": _clean_list(part04.get("must"), 5),
            "plus": _clean_list(part04.get("plus"), 5),
            "flexible": _clean_list(part04.get("flexible"), 5),
        },
        "part05": {
            "timeline": _clean_list(part05.get("timeline"), 5),
            "growth": _clean_list(part05.get("growth"), 5),
        },
        "part06": {"issues": issues, "tactics": _clean_list(part06.get("tactics"), 5)},
        "part07": {
            "path": _clean_list(part07.get("path"), 5),
            "closing": str(part07.get("closing") or "").strip()[:300],
        },
        "match_focus": _clean_list(parsed.get("match_focus"), 3),
    }


async def _next_version(db: AsyncSession, user_id: int) -> int:
    row = (
        await db.execute(
            text("SELECT COALESCE(MAX(version), 0) AS v FROM aibot_love_report WHERE user_id = :uid"),
            {"uid": user_id},
        )
    ).mappings().first()
    return int(row["v"] or 0) + 1 if row else 1


def _profile_rows(member_facts: dict[str, Any], ai_lines: list[str]) -> list[dict[str, str]]:
    """PART 01 的键值行：只列真实有的字段，缺的不占位。"""
    mapping = (
        ("性别", "gender"),
        ("年龄段", "age_band"),
        ("生日", "birthday"),
        ("身高", "height_cm"),
        ("体重", "weight_kg"),
        ("学历", "education_level"),
        ("职业", "occupation"),
        ("收入", "income"),
        ("城市", "city"),
    )
    rows: list[dict[str, str]] = []
    for label, key in mapping:
        value = member_facts.get(key)
        if value is None or str(value).strip() == "":
            continue
        rows.append({"k": label, "v": str(value)[:32]})
    rows.append({"k": "AI 画像", "v": f"{len(ai_lines)} 条已发布" if ai_lines else "未建构"})
    return rows


async def generate_report(
    db: AsyncSession,
    user_id: int,
    *,
    client_score: dict[str, Any] | None = None,
    love_index: dict[str, Any] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """生成一版报告并落库；同一天多次生成按版本递增，历史全部可回看。"""
    from app.services.aibot_chat import get_assignment

    quota_key = daily_quota_key(_REPORT_QUOTA_PREFIX, user_id)
    if not await consume_daily(quota_key, _REPORT_DAILY_LIMIT):
        raise HTTPException(
            429, detail=f"今日报告生成已达 {_REPORT_DAILY_LIMIT} 次上限，历史版本仍可查看"
        )
    try:
        return await _compose_and_store(
            db,
            user_id,
            client_score=client_score,
            love_index=love_index,
            agent_id=agent_id,
            assignment=await get_assignment(db, user_id),
        )
    except Exception:
        # 生成失败不该吃额度：模型/素材问题修好后当天重试仍应可用
        try:
            await refund_daily(quota_key)
        except Exception:
            logger.warning("aibot report quota refund failed", exc_info=True)
        raise


async def _compose_and_store(
    db: AsyncSession,
    user_id: int,
    *,
    client_score: dict[str, Any] | None,
    love_index: dict[str, Any] | None,
    agent_id: str | None,
    assignment: Any,
) -> dict[str, Any]:
    nickname = await _load_nickname(db, user_id)
    member_facts = await _load_member_facts(db, user_id)
    ai_lines = await _load_ai_profile_lines(db, user_id)
    chat_lines, user_turns = await _load_chat_digest(db, user_id)

    resolved_agent = agent_id or getattr(assignment, "agent_id", None) or "anian"
    meta = get_agent_meta(resolved_agent)

    score = client_score if isinstance(client_score, dict) else {}
    bars = _normalize_bars(score.get("bars"))
    try:
        score_total = int(score.get("total") or 0)
    except Exception:
        score_total = 0
    total_for_prompt: Any = score_total if score_total > 0 else "未提供（按资料定性描述，不要写数字）"

    li = love_index if isinstance(love_index, dict) else {}
    try:
        li_value = round(float(li.get("value") or 0), 2)
    except Exception:
        li_value = 0.0
    li_label = str(li.get("intentLabel") or "未明确").strip()[:24]
    li_state = str(li.get("state") or ("积极期" if li_value >= 0 else "观望期")).strip()[:16]

    source = "ai"
    narrative: dict[str, Any]
    messages = build_report_messages(
        agent_id=resolved_agent,
        nickname=nickname,
        member_facts=member_facts,
        ai_lines=ai_lines,
        chat_lines=chat_lines,
        score_total=total_for_prompt,
        love_index_label=li_label,
    )
    try:
        raw = await complete(
            messages,
            json_mode=True,
            scene="aibot_love_report",
            feature=AiFeature.ADVISOR,
        )
        parsed = parse_json(raw)
        if not str(parsed.get("letter") or "").strip():
            raise ValueError("empty letter")
        narrative = _normalize_narrative(parsed)
    except HTTPException:
        raise
    except Exception:
        logger.warning("aibot report fallback to rule narrative user=%s", user_id, exc_info=True)
        source = "rule"
        narrative = _rule_narrative(
            nickname=nickname,
            title="先生" if str(member_facts.get("gender") or "女") == "男" else "女士",
            member_facts=member_facts,
            ai_lines=ai_lines,
            emotion=_rule_emotion(chat_lines),
            love_index_label=li_label,
        )

    if not narrative["emotion"].get("evidence"):
        narrative["emotion"] = _rule_emotion(chat_lines)

    version = await _next_version(db, user_id)
    payload: dict[str, Any] = {
        "version": version,
        "brand": {"org": "宣誓爱", "advisor": meta["name"], "title": "AI 婚恋顾问"},
        "member": {
            "name": nickname,
            "title": "先生" if str(member_facts.get("gender") or "女") == "男" else "女士",
            "id": f"XS-{user_id}",
        },
        "date": _report_date(),
        "score": {
            "total": score_total,
            "raw": score.get("raw"),
            "skipped": _clean_list(score.get("skipped"), 12),
            "bars": bars,
        },
        "loveIndex": {
            "value": li_value,
            "intent": str(li.get("intent") or "unsure")[:24],
            "intentLabel": li_label,
            "state": li_state,
        },
        "letter": narrative["letter"],
        "part01": {
            "profile": _profile_rows(member_facts, ai_lines),
            "strengths": narrative["part01"]["strengths"],
            "summary": narrative["part01"]["summary"],
        },
        "part02": narrative["part02"],
        "part03": narrative["part03"],
        "part04": narrative["part04"],
        "part05": narrative["part05"],
        "part06": narrative["part06"],
        "part07": narrative["part07"],
        "part08": {
            "emotion": narrative["emotion"],
            "sources": {
                "profile_fields": len(member_facts),
                "ai_profile_entries": len(ai_lines),
                "chat_turns": user_turns,
                "scene": source,
            },
        },
        "matchFocus": narrative["match_focus"],
        "changelog": "按最新资料、AI 画像与最近对话重新整理" if version > 1 else None,
        "generatedAt": int(datetime.now().timestamp() * 1000),
    }

    await db.execute(
        text(
            """INSERT INTO aibot_love_report
                   (user_id, version, agent_id, scene, score_total, love_index,
                    emotion_label, emotion_score, payload_json)
               VALUES
                   (:uid, :version, :agent_id, :scene, :score_total, :love_index,
                    :emotion_label, :emotion_score, :payload)"""
        ),
        {
            "uid": user_id,
            "version": version,
            "agent_id": resolved_agent,
            "scene": source,
            "score_total": score_total,
            "love_index": li_value,
            "emotion_label": narrative["emotion"]["label"][:32],
            "emotion_score": narrative["emotion"]["score"],
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    await db.commit()
    return payload


async def list_reports(db: AsyncSession, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                """SELECT version, scene, score_total, love_index, emotion_label, created_at
                   FROM aibot_love_report
                   WHERE user_id = :uid
                   ORDER BY version DESC
                   LIMIT :lim"""
            ),
            {"uid": user_id, "lim": max(1, min(limit, 50))},
        )
    ).mappings().all()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "version": int(row["version"]),
                "scene": str(row.get("scene") or "ai"),
                "score_total": int(row.get("score_total") or 0),
                "love_index": float(row.get("love_index") or 0),
                "emotion_label": str(row.get("emotion_label") or ""),
                "created_at": row["created_at"].isoformat(sep=" ", timespec="seconds")
                if hasattr(row.get("created_at"), "isoformat")
                else str(row.get("created_at") or ""),
            }
        )
    return out


async def get_report(db: AsyncSession, user_id: int, version: int) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """SELECT payload_json FROM aibot_love_report
                   WHERE user_id = :uid AND version = :version LIMIT 1"""
            ),
            {"uid": user_id, "version": version},
        )
    ).mappings().first()
    if not row:
        raise HTTPException(404, detail="报告版本不存在")
    payload = row["payload_json"]
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception as exc:
            raise HTTPException(500, detail="报告数据损坏") from exc
    return payload if isinstance(payload, dict) else {}


async def latest_report(db: AsyncSession, user_id: int) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                """SELECT version FROM aibot_love_report
                   WHERE user_id = :uid ORDER BY version DESC LIMIT 1"""
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    if not row:
        return None
    return await get_report(db, user_id, int(row["version"]))


async def create_report_invite_wxacode() -> dict[str, str]:
    """报告分享/导出用的邀请小程序码（getwxacodeunlimit）。

    scene 固定 ``xsa=report``，落地页走恋 AI BOT 入口；微信凭据未配置时 503。
    """
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise HTTPException(503, detail="微信小程序码服务未配置")
    page = "pages/auth/aibot-entry"
    scene = "xsa=report"
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": settings.wechat_app_id,
                "secret": settings.wechat_app_secret,
            },
        )
        token_data = token_response.json()
        if token_data.get("errcode") or not token_data.get("access_token"):
            raise HTTPException(503, detail="微信访问令牌获取失败")
        qr_response = await client.post(
            f"https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={token_data['access_token']}",
            json={
                "scene": scene,
                "page": page,
                "check_path": False,
                "env_version": "release",
            },
        )
    if "image" not in qr_response.headers.get("content-type", ""):
        logger.warning(
            "aibot invite wxacode failed content-type=%s body=%s",
            qr_response.headers.get("content-type"),
            qr_response.text[:200] if qr_response.text else "",
        )
        raise HTTPException(503, detail="微信小程序码生成失败")
    return {
        "scene": scene,
        "page": page,
        "image": "data:image/png;base64," + base64.b64encode(qr_response.content).decode("ascii"),
    }
