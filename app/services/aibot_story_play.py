"""恋 AI BOT 剧本游玩落库 + 演完后同本同好匹配（真实用户，无 Demo 假人）。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.aibot_profile_sync import parse_bot_intake_dict

logger = logging.getLogger(__name__)

_VALID_STATUS = frozenset({"playing", "paused", "finished"})


def _age_from_birthday(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        if hasattr(raw, "year"):
            b = raw
        else:
            s = str(raw).strip()[:10]
            b = date.fromisoformat(s)
        today = date.today()
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    except Exception:
        return None


async def upsert_story_play(
    db: AsyncSession,
    user_id: int,
    task_id: str,
    *,
    status: str = "playing",
    chapter_idx: int = 0,
) -> dict[str, Any]:
    """开玩 / 推进 / 暂停 / 演完：同一 user+task 一行，更新活跃与状态。"""
    tid = (task_id or "").strip()[:64]
    if not tid:
        return {"ok": False, "reason": "missing_task"}
    st = (status or "playing").strip()[:16]
    if st not in _VALID_STATUS:
        st = "playing"
    ch = max(0, int(chapter_idx or 0))
    await db.execute(
        text(
            """INSERT INTO aibot_story_play
                   (user_id, task_id, status, chapter_idx, started_at, last_active_at, finished_at)
               VALUES
                   (:uid, :tid, :st, :ch, UTC_TIMESTAMP(), UTC_TIMESTAMP(),
                    IF(:st = 'finished', UTC_TIMESTAMP(), NULL))
               ON DUPLICATE KEY UPDATE
                   status = VALUES(status),
                   chapter_idx = VALUES(chapter_idx),
                   last_active_at = UTC_TIMESTAMP(),
                   finished_at = IF(VALUES(status) = 'finished',
                                    COALESCE(finished_at, UTC_TIMESTAMP()),
                                    finished_at)"""
        ),
        {"uid": user_id, "tid": tid, "st": st, "ch": ch},
    )
    await db.commit()
    return {"ok": True, "task_id": tid, "status": st, "chapter_idx": ch}


async def _viewer_prefer_gender(db: AsyncSession, user_id: int) -> tuple[int | None, int | None, str | None]:
    """返回 (prefer_gender 1|2|None 不限, viewer_gender, city_code)。"""
    row = (
        await db.execute(
            text(
                """SELECT u.gender, p.bot_intake_json, p.residence_city_code,
                          pref.preferred_city_codes
                   FROM users u
                   LEFT JOIN user_profile p ON p.user_id = u.id
                   LEFT JOIN user_partner_preference pref ON pref.user_id = u.id
                   WHERE u.id = :uid LIMIT 1"""
            ),
            {"uid": user_id},
        )
    ).mappings().first()
    if not row:
        return None, None, None
    viewer_gender = int(row["gender"]) if row.get("gender") in (1, 2) else None
    city = str(row["residence_city_code"]).strip() if row.get("residence_city_code") else None
    bot = parse_bot_intake_dict(row.get("bot_intake_json"))
    prefer_raw = bot.get("preferGender")
    prefer_id = ""
    if isinstance(prefer_raw, dict):
        prefer_id = str(prefer_raw.get("id") or "").strip()
    elif prefer_raw is not None:
        prefer_id = str(prefer_raw).strip()
    prefer_gender: int | None = None
    if prefer_id in ("男", "1"):
        prefer_gender = 1
    elif prefer_id in ("女", "2"):
        prefer_gender = 2
    elif prefer_id in ("不限", "any"):
        prefer_gender = None
    elif viewer_gender in (1, 2):
        # 未填期望性别：默认异性
        prefer_gender = 2 if viewer_gender == 1 else 1
    return prefer_gender, viewer_gender, city


async def _mark_shown(db: AsyncSession, user_id: int, task_id: str, peer_user_id: int) -> None:
    await db.execute(
        text(
            """INSERT INTO aibot_story_peer_shown (user_id, task_id, peer_user_id, shown_at)
               VALUES (:uid, :tid, :peer, UTC_TIMESTAMP())
               ON DUPLICATE KEY UPDATE shown_at = UTC_TIMESTAMP()"""
        ),
        {"uid": user_id, "tid": task_id, "peer": peer_user_id},
    )
    await db.commit()


async def match_story_peer(
    db: AsyncSession,
    viewer_id: int,
    task_id: str,
    *,
    task_title: str = "",
) -> dict[str, Any] | None:
    """从「玩过同一剧本」的真实用户里，按喜好筛 1 人；没有则 None。"""
    tid = (task_id or "").strip()[:64]
    if not tid:
        return None
    prefer_gender, _viewer_gender, viewer_city = await _viewer_prefer_gender(db, viewer_id)

    rows = (
        await db.execute(
            text(
                """SELECT sp.user_id, sp.last_active_at, sp.finished_at, sp.status,
                          u.nickname, u.gender, u.birthday, u.avatar, u.is_married,
                          p.occupation, p.residence_city_code, p.residence,
                          p.height, p.education_level, p.interest_tags
                   FROM aibot_story_play sp
                   INNER JOIN users u ON u.id = sp.user_id
                   LEFT JOIN user_profile p ON p.user_id = u.id
                   WHERE sp.task_id = :tid
                     AND sp.user_id <> :uid
                     AND NOT EXISTS (
                       SELECT 1 FROM aibot_story_peer_shown s
                       WHERE s.user_id = :uid AND s.task_id = :tid
                         AND s.peer_user_id = sp.user_id
                     )
                   ORDER BY sp.last_active_at DESC
                   LIMIT 40"""
            ),
            {"tid": tid, "uid": viewer_id},
        )
    ).mappings().all()
    if not rows:
        return None

    scored: list[tuple[int, Any]] = []
    for row in rows:
        g = row.get("gender")
        if prefer_gender in (1, 2) and g not in (1, 2):
            continue
        if prefer_gender in (1, 2) and int(g) != prefer_gender:
            continue
        score = 50
        peer_city = str(row["residence_city_code"]).strip() if row.get("residence_city_code") else ""
        if viewer_city and peer_city and viewer_city[:4] == peer_city[:4]:
            score += 20
        if row.get("finished_at") is not None:
            score += 5
        if str(row.get("status") or "") == "playing":
            score += 3
        # 最近活跃略加分
        la = row.get("last_active_at")
        if la is not None and hasattr(la, "timestamp"):
            try:
                age_h = (datetime.utcnow() - la.replace(tzinfo=None)).total_seconds() / 3600.0
                if age_h < 24:
                    score += 10
                elif age_h < 168:
                    score += 5
            except Exception:
                pass
        scored.append((score, row))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, pick = scored[0]
    peer_id = int(pick["user_id"])
    await _mark_shown(db, viewer_id, tid, peer_id)

    age = _age_from_birthday(pick.get("birthday"))
    city = ""
    try:
        from app.services.regions import region_display

        code = str(pick.get("residence_city_code") or "").strip()
        province = code[:2] if len(code) >= 2 else None
        city = region_display(province, code or None, None) or (
            str(pick["residence"]).strip() if pick.get("residence") else ""
        )
    except Exception:
        city = str(pick["residence"]).strip() if pick.get("residence") else ""

    gender_label = "男" if pick.get("gender") == 1 else ("女" if pick.get("gender") == 2 else "")
    title = (task_title or "").strip() or tid
    return {
        "userId": peer_id,
        "user_id": peer_id,
        "name": str(pick.get("nickname") or "会员"),
        "nickname": str(pick.get("nickname") or "会员"),
        "age": str(age) if age is not None else "",
        "gender": gender_label,
        "city": city,
        "job": str(pick.get("occupation") or ""),
        "occupation": str(pick.get("occupation") or ""),
        "avatar": str(pick.get("avatar") or ""),
        "height": int(pick["height"]) if pick.get("height") is not None else 0,
        "education_level": pick.get("education_level"),
        "is_married": pick.get("is_married"),
        "interest_tags": pick.get("interest_tags"),
        "score": min(99, max(60, best_score)),
        "reason": f"也玩过《{title}》",
        "storyBadge": True,
        "peerId": str(peer_id),
        "taskId": tid,
    }
