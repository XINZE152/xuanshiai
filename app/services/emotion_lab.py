"""情感实验室 E1.5：把端侧测出的 MBTI 同步进个人资料。

范围刻意只有「写 `user_profile.mbti`」这一件事：

- 不建会话/不存答题/不做合拍（E2 才需要）；
- 不写 `user_mbti_result`：该表只在 legacy 的 ``database_setup_marriage.py`` 里定义，
  不在 ``app/db/business_schema.py``，新环境并不存在；等 E2 落库时再进 schema + 迁移；
- 写资料复用 ``profile.update_profile``，因此完整度重算、修订号入队、锁定顺序都与其他
  资料字段完全一致，不另开一条旁路。
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import ProfileUpdateRequest
from app.schemas.emotion_lab import (
    EmotionLabMatchItem,
    EmotionLabMatchesResponse,
    EmotionLabProfileSourceRequest,
    EmotionLabProfileSourceResponse,
    EmotionLabAnswer,
    EmotionLabAssessment,
    EmotionLabAnswersRequest,
    EmotionLabProfileSource,
    EmotionLabSession,
    EmotionLabSummaryResponse,
)
from app.services.discovery import emotion_lab_matches as _rank_matches
from app.services.profile import update_profile


ASSESSMENT_ID = "mbti-core"
ASSESSMENT_VERSION = "mbti-core@3"
RESULT_COPY_VERSION = "mbti-result-copy@2"
DISCLAIMER = "结果仅用于自我了解，不构成心理诊断或专业建议。"
OPTION_LABELS = ["非常不同意", "不同意", "比较不同意", "不确定", "比较同意", "同意", "非常同意"]
POLES = {"EI": ("E", "I"), "SN": ("S", "N"), "TF": ("T", "F"), "JP": ("J", "P")}

# 题目快照由服务端控制，提交时按 snapshot 计分；客户端只收到 text/options。
QUESTION_ROWS: tuple[tuple[str, str, str, int], ...] = (
    ("mbti-16p-001", "您经常主动认识新朋友。", "EI", 1),
    ("mbti-16p-002", "您喜欢探索新主题和新想法。", "SN", -1),
    ("mbti-16p-003", "做决定时，您会优先考虑他人的感受。", "TF", -1),
    ("mbti-16p-004", "您喜欢提前安排好接下来的计划。", "JP", 1),
    ("mbti-16p-005", "在压力下，您通常能保持冷静。", "TF", 1),
    ("mbti-16p-006", "社交活动中，您更愿意和熟人待在一起。", "EI", -1),
    ("mbti-16p-007", "您通常会把一件事完整做完再开始下一件。", "JP", 1),
    ("mbti-16p-008", "别人的情绪很容易影响到您。", "TF", -1),
    ("mbti-16p-009", "日程表和清单能让您更安心。", "JP", 1),
    ("mbti-16p-010", "一个小错误也会让您反复怀疑自己。", "TF", -1),
    ("mbti-16p-011", "您能自然地向感兴趣的人开启对话。", "EI", 1),
    ("mbti-16p-012", "您更关注具体事实，而不是象征和隐喻。", "SN", 1),
    ("mbti-16p-013", "您更习惯用逻辑而不是直觉做判断。", "TF", 1),
    ("mbti-16p-014", "您更喜欢随心安排，而不是严格按计划生活。", "JP", -1),
    ("mbti-16p-015", "您很少担心自己给别人留下什么印象。", "TF", 1),
    ("mbti-16p-016", "参加集体活动会让您获得能量。", "EI", 1),
    ("mbti-16p-017", "您喜欢留白多、可以自行解读的作品。", "SN", -1),
    ("mbti-16p-018", "帮助别人完成事情会让您很有成就感。", "TF", -1),
    ("mbti-16p-019", "选择太多时，您很难决定下一步做什么。", "JP", -1),
    ("mbti-16p-020", "您很容易担心事情会变得更糟。", "TF", -1),
    ("mbti-16p-021", "在团队里，您会主动承担带头的角色。", "EI", 1),
    ("mbti-16p-022", "您喜欢动手解决看得见的实际问题。", "SN", 1),
    ("mbti-16p-023", "面对冲突时，您会先分析谁更有道理。", "TF", 1),
    ("mbti-16p-024", "放松之前先完成家务会让您更舒服。", "JP", 1),
    ("mbti-16p-025", "您喜欢观察不同观点之间的辩论。", "TF", 1),
    ("mbti-16p-026", "您倾向于避免成为众人关注的中心。", "EI", -1),
    ("mbti-16p-027", "您的情绪状态会在一天内快速变化。", "TF", -1),
    ("mbti-16p-028", "低效率会让您很难保持耐心。", "JP", 1),
    ("mbti-16p-029", "您经常拖到最后期限前才开始行动。", "JP", -1),
    ("mbti-16p-030", "您常常思考人生意义和未来可能性。", "SN", -1),
)

SUMMARY_COPY = {
    "INTJ": "您倾向于独立地把复杂想法转化为清晰的行动计划。",
    "INTP": "您倾向于探索抽象概念，重视分析、知识与创造性地解决问题。",
    "ENTJ": "您倾向于组织并推动结果，关注目标、执行和长远方向。",
    "ENTP": "您倾向于从不同角度探索可能性，享受思考、讨论和创新。",
    "INFJ": "您倾向于从价值与洞察出发理解问题，并关注有意义的影响。",
    "INFP": "您倾向于忠于内在价值，重视创造力、同理心和个人意义。",
    "ENFJ": "您倾向于关注人与关系，擅长鼓励、引导并凝聚他人。",
    "ENFP": "您倾向于保持好奇与想象力，并在人际互动中发现新的可能。",
    "ISTJ": "您倾向于重视秩序、责任和可靠的执行，习惯把事情做得明确。",
    "ISFJ": "您倾向于以细致可靠的方式关心和支持他人，也重视承诺。",
    "ESTJ": "您倾向于组织资源并推动结果，重视清晰的规则和行动。",
    "ESFJ": "您倾向于关注社区与和谐，善于察觉需求并维护联系。",
    "ISTP": "您倾向于以冷静、务实的方式理解事物，并灵活解决问题。",
    "ISFP": "您倾向于尊重内在感受与审美，以温和而自主的方式作出选择。",
    "ESTP": "您倾向于在当下行动、适应变化，并从实践和挑战中获得能量。",
    "ESFP": "您倾向于投入当下体验和人际互动，并为周围带来活力。",
}


def _unprocessable(code: str, message: str) -> HTTPException:
    """带业务码的 422（与 ``_forbidden`` 同形，前端可按 code 分流）。"""
    return HTTPException(422, detail={"code": code, "message": message, "retryable": False})


def _assessment() -> EmotionLabAssessment:
    return EmotionLabAssessment(
        id=ASSESSMENT_ID,
        version=ASSESSMENT_VERSION,
        title="MBTI 基础维度测试",
        authorization={"status": "approved", "label": "内容已授权", "reviewedAt": None},
        canStart=True,
        questionCount=len(QUESTION_ROWS),
        scale={"min": 1, "max": 7},
        dimensions=["EI", "SN", "TF", "JP"],
        tieBreak="first_pole",
        resultCopyVersion=RESULT_COPY_VERSION,
    )


def _questions() -> list[dict]:
    options = [{"value": i, "label": label} for i, label in enumerate(OPTION_LABELS, 1)]
    return [
        {
            "id": question_id,
            "text": text_value,
            "options": options,
            "dimension": dimension,
            "direction": direction,
            "scaleMin": 1,
            "scaleMax": 7,
        }
        for question_id, text_value, dimension, direction in QUESTION_ROWS
    ]


def _public_snapshot(row: dict) -> EmotionLabSession:
    payload = row.get("payload") or {}
    questions = payload.get("questions") or []
    answers = payload.get("answers") or []
    return EmotionLabSession(
        schemaVersion=2,
        id=str(row["id"]),
        definitionId=str(row["assessment_id"]),
        definitionVersion=str(row["assessment_version"]),
        resultCopyVersion=str(row["result_copy_version"]),
        kind="mbti",
        status=str(row["status"]),
        questionIds=[str(item["id"]) for item in questions],
        questions=[{key: value for key, value in item.items() if key not in {"dimension", "direction", "scaleMin", "scaleMax"}} for item in questions],
        answers=[EmotionLabAnswer.model_validate(item) for item in answers],
        result=row.get("result"),
        createdAt=row["created_at"],
        updatedAt=row["updated_at"],
    )


def _decode_row(row: object) -> dict:
    data = dict(row) if row is not None else {}
    for key in ("payload", "result"):
        value = data.get(key)
        if isinstance(value, str):
            try:
                data[key] = json.loads(value)
            except json.JSONDecodeError:
                data[key] = {}
    return data


async def _session_row(db: AsyncSession, user_id: int, session_id: str) -> dict | None:
    result = await db.execute(
        text("SELECT * FROM emotion_lab_session WHERE id = :id AND user_id = :user_id LIMIT 1"),
        {"id": session_id, "user_id": user_id},
    )
    row = result.mappings().first()
    return _decode_row(row) if row else None


def _merged_answers(snapshot: dict, incoming: list[EmotionLabAnswer]) -> list[dict]:
    questions = snapshot.get("payload", {}).get("questions", [])
    allowed = {str(item["id"]): item for item in questions}
    merged = {str(item["questionId"]): int(item["value"]) for item in snapshot.get("payload", {}).get("answers", [])}
    for answer in incoming:
        key = str(answer.question_id)
        question = allowed.get(key)
        if question is None:
            raise _unprocessable("QUESTION_NOT_IN_SESSION", "答案不属于当前测试会话")
        value = int(answer.value)
        if value < int(question.get("scaleMin", 1)) or value > int(question.get("scaleMax", 7)):
            raise _unprocessable("ANSWER_VALUE_INVALID", "答案不在当前题目的量表范围内")
        merged[key] = value
    return [{"questionId": str(item["id"]), "value": merged[str(item["id"])]} for item in questions if str(item["id"]) in merged]


def _score_snapshot(snapshot: dict, answers: list[dict]) -> dict:
    answer_map = {str(item["questionId"]): int(item["value"]) for item in answers}
    dimensions: dict[str, dict[str, int]] = {}
    mbti = ""
    for dimension, poles in POLES.items():
        rows = [item for item in snapshot["payload"]["questions"] if item.get("dimension") == dimension]
        raw = 0
        possible = 0
        for item in rows:
            if str(item["id"]) not in answer_map:
                continue
            span = int(item.get("scaleMax", 7)) - int(item.get("scaleMin", 1))
            raw += (answer_map[str(item["id"])] - 1) if int(item.get("direction", 1)) == 1 else (7 - answer_map[str(item["id"])] )
            possible += span
        if possible == 0:
            # Partial checkpoints can contain no answer for a later dimension.
            first_score = 50
        else:
            first_score = round(raw / possible * 100)
        second_score = 100 - first_score
        dimensions[dimension] = {poles[0]: first_score, poles[1]: second_score}
        mbti += poles[0] if first_score >= second_score else poles[1]
    result_id = "mbti-result:" + str(snapshot["id"]) + ":" + str(len(answers))
    return {
        "id": result_id,
        "sessionId": str(snapshot["id"]),
        "assessmentId": ASSESSMENT_ID,
        "assessmentVersion": ASSESSMENT_VERSION,
        "source": "assessment",
        "mbtiType": mbti,
        "dimensions": dimensions,
        "resultCopy": {
            "version": RESULT_COPY_VERSION,
            "title": mbti + " · MBTI 偏好结果",
            "summary": SUMMARY_COPY.get(mbti, "四个字母记录你在四组维度上的当前偏好，不代表能力高低或固定人格。"),
            "disclaimer": DISCLAIMER,
        },
        "answeredCount": len(answers),
        "totalCount": len(snapshot["payload"]["questions"]),
        "provisional": len(answers) < len(snapshot["payload"]["questions"]),
        "completedAt": datetime.now(UTC).isoformat(),
    }


async def _profile_row(db: AsyncSession, user_id: int) -> dict | None:
    result = await db.execute(
        text("SELECT mbti, updated_at FROM user_profile WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def summary(db: AsyncSession, user_id: int) -> EmotionLabSummaryResponse:
    profile = await _profile_row(db, user_id)
    if profile is None:
        raise HTTPException(404, detail="资料不存在，请先完成注册")
    active_result = await db.execute(
        text("SELECT * FROM emotion_lab_session WHERE user_id = :user_id AND status = 'in_progress' ORDER BY updated_at DESC LIMIT 1"),
        {"user_id": user_id},
    )
    completed_result = await db.execute(
        text("SELECT * FROM emotion_lab_session WHERE user_id = :user_id AND status = 'completed' ORDER BY completed_at DESC, updated_at DESC LIMIT 1"),
        {"user_id": user_id},
    )
    active_row = active_result.mappings().first()
    completed_row = completed_result.mappings().first()
    latest = _decode_row(completed_row) if completed_row else None
    profile_source = None
    if profile.get("mbti"):
        latest_result = latest.get("result") if latest else None
        profile_source = EmotionLabProfileSource(
            mbtiType=str(profile["mbti"]),
            source="assessment" if latest_result and latest_result.get("mbtiType") == profile["mbti"] else "self_reported",
            assessmentVersion=latest_result.get("assessmentVersion") if latest_result else None,
            resultId=latest_result.get("id") if latest_result else None,
            confirmedAt=profile.get("updated_at"),
        )
    return EmotionLabSummaryResponse(
        assessments=[_assessment()],
        activeSession=_public_snapshot(_decode_row(active_row)) if active_row else (_public_snapshot(latest) if latest else None),
        profileSource=profile_source,
        disclaimer=DISCLAIMER,
        disclaimerVersion=RESULT_COPY_VERSION,
    )


async def start_session(db: AsyncSession, user_id: int, assessment_id: str) -> EmotionLabSession:
    if assessment_id != ASSESSMENT_ID:
        raise _unprocessable("ASSESSMENT_NOT_FOUND", "测试版本不存在")
    profile = await _profile_row(db, user_id)
    if profile is None:
        raise HTTPException(404, detail="资料不存在，请先完成注册")
    existing = await db.execute(
        text("SELECT * FROM emotion_lab_session WHERE user_id = :user_id AND assessment_id = :assessment_id AND status = 'in_progress' ORDER BY updated_at DESC LIMIT 1"),
        {"user_id": user_id, "assessment_id": ASSESSMENT_ID},
    )
    row = existing.mappings().first()
    if row:
        return _public_snapshot(_decode_row(row))
    now = datetime.now(UTC).replace(tzinfo=None)
    session_id = str(uuid4())
    payload = {"questions": _questions(), "answers": []}
    await db.execute(
        text("""INSERT INTO emotion_lab_session
            (id, user_id, assessment_id, assessment_version, result_copy_version, status, payload, result, created_at, updated_at)
            VALUES (:id, :user_id, :assessment_id, :assessment_version, :result_copy_version, 'in_progress', :payload, NULL, :created_at, :updated_at)"""),
        {"id": session_id, "user_id": user_id, "assessment_id": ASSESSMENT_ID, "assessment_version": ASSESSMENT_VERSION,
         "result_copy_version": RESULT_COPY_VERSION, "payload": json.dumps(payload, ensure_ascii=False), "created_at": now, "updated_at": now},
    )
    await db.commit()
    row = await _session_row(db, user_id, session_id)
    return _public_snapshot(row or {})


async def save_answers(db: AsyncSession, user_id: int, session_id: str, request: EmotionLabAnswersRequest) -> EmotionLabSession:
    snapshot = await _session_row(db, user_id, session_id)
    if snapshot is None:
        raise HTTPException(404, detail="测试会话不存在")
    if snapshot["status"] == "completed":
        raise _unprocessable("SESSION_ALREADY_COMPLETED", "测试已经提交")
    if snapshot["status"] == "discarded":
        raise _unprocessable("SESSION_DISCARDED", "测试会话已放弃")
    answers = _merged_answers(snapshot, request.answers)
    payload = snapshot["payload"]
    payload["answers"] = answers
    now = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(text("UPDATE emotion_lab_session SET payload = :payload, updated_at = :updated_at WHERE id = :id AND user_id = :user_id"),
                     {"payload": json.dumps(payload, ensure_ascii=False), "updated_at": now, "id": session_id, "user_id": user_id})
    await db.commit()
    row = await _session_row(db, user_id, session_id)
    return _public_snapshot(row or {})


async def submit_session(db: AsyncSession, user_id: int, session_id: str, request: EmotionLabAnswersRequest) -> EmotionLabSession:
    snapshot = await _session_row(db, user_id, session_id)
    if snapshot is None:
        raise HTTPException(404, detail="测试会话不存在")
    if snapshot["status"] == "discarded":
        raise _unprocessable("SESSION_DISCARDED", "测试会话已放弃")
    answers = _merged_answers(snapshot, request.answers)
    total = len(snapshot["payload"]["questions"])
    if len(answers) < 10:
        raise _unprocessable("ANSWERS_CHECKPOINT_REQUIRED", "至少完成 10 题后才能查看阶段结果")
    if len(answers) < total and len(answers) % 10 != 0:
        raise _unprocessable("ANSWERS_CHECKPOINT_REQUIRED", "请完成下一个 10 题阶段后查看结果")
    result = _score_snapshot(snapshot, answers)
    status = "completed" if len(answers) == total else "in_progress"
    payload = snapshot["payload"]
    payload["answers"] = answers
    now = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(text("""UPDATE emotion_lab_session SET payload = :payload, result = :result,
        status = :status, updated_at = :updated_at, completed_at = CASE WHEN :status = 'completed' THEN :updated_at ELSE completed_at END
        WHERE id = :id AND user_id = :user_id"""),
                     {"payload": json.dumps(payload, ensure_ascii=False), "result": json.dumps(result, ensure_ascii=False),
                      "status": status, "updated_at": now, "id": session_id, "user_id": user_id})
    await db.commit()
    row = await _session_row(db, user_id, session_id)
    return _public_snapshot(row or {})


async def discard_session(db: AsyncSession, user_id: int, session_id: str) -> EmotionLabSession:
    snapshot = await _session_row(db, user_id, session_id)
    if snapshot is None:
        raise HTTPException(404, detail="测试会话不存在")
    if snapshot["status"] == "completed":
        raise _unprocessable("SESSION_ALREADY_COMPLETED", "已完成测试不能放弃")
    await db.execute(text("UPDATE emotion_lab_session SET status = 'discarded', updated_at = :updated_at WHERE id = :id AND user_id = :user_id"),
                     {"updated_at": datetime.now(UTC).replace(tzinfo=None), "id": session_id, "user_id": user_id})
    await db.commit()
    row = await _session_row(db, user_id, session_id)
    return _public_snapshot(row or {})


async def sync_profile_source(
    db: AsyncSession, user_id: int, request: EmotionLabProfileSourceRequest
) -> EmotionLabProfileSourceResponse:
    """把 MBTI 写进资料；同一结果重复提交不产生任何副作用。"""
    if not request.confirmed:
        raise _unprocessable("PROFILE_SYNC_CONFIRMATION_REQUIRED", "同步资料前需要明确确认")
    if request.source == "assessment" and not (request.result_id or "").strip():
        raise _unprocessable("ASSESSMENT_RESULT_REQUIRED", "同步测评结果需要有效结果记录")

    row = await _profile_row(db, user_id)
    if row is None:
        raise HTTPException(404, detail="资料不存在，请先完成注册")

    assessment_version: str | None = None
    if request.source == "assessment":
        result = await db.execute(
            text("""SELECT id, status, result FROM emotion_lab_session
                    WHERE user_id = :user_id AND status = 'completed'
                      AND (id = :result_id
                           OR JSON_UNQUOTE(JSON_EXTRACT(result, '$.id')) = :result_id)
                    LIMIT 1"""),
            {"result_id": request.result_id, "user_id": user_id},
        )
        result_row = result.mappings().first()
        if not result_row:
            raise _unprocessable("ASSESSMENT_RESULT_INVALID", "测评结果不存在或尚未完成")
        result_data = _decode_row(result_row).get("result") or {}
        if result_data.get("id") != request.result_id and str(result_row.get("id")) != request.result_id:
            raise _unprocessable("ASSESSMENT_RESULT_INVALID", "测评结果记录不匹配")
        if result_data.get("provisional") is True or result_data.get("mbtiType") != request.mbti_type:
            raise _unprocessable("ASSESSMENT_RESULT_INVALID", "只能同步完整且匹配的测评结果")
        assessment_version = str(result_data.get("assessmentVersion") or "") or None

    confirmed_at: datetime | None = row.get("updated_at")
    if row.get("mbti") != request.mbti_type:
        # 复用资料更新：完整度重算（mbti 是 18 项之一）、修订号入队、锁序都走既有路径。
        await update_profile(db, user_id, ProfileUpdateRequest(mbti=request.mbti_type))
        refreshed = await _profile_row(db, user_id)
        confirmed_at = (refreshed or {}).get("updated_at") or datetime.now(UTC).replace(tzinfo=None)
    else:
        # 幂等：资料没变就不写库，返回上次变更时间。
        confirmed_at = confirmed_at or datetime.now(UTC).replace(tzinfo=None)

    return EmotionLabProfileSourceResponse(
        mbti_type=request.mbti_type,
        source=request.source,
        assessment_version=assessment_version,
        result_id=request.result_id,
        confirmed_at=confirmed_at,
    )


async def matches(
    db: AsyncSession, user_id: int, limit: int = 6
) -> EmotionLabMatchesResponse:
    """「合拍的人」：自身 MBTI 为空时返回空列表 + 可展示提示，不抛 404。

    排序复用 ``services.discovery.emotion_lab_matches``（内部即首页卡片的
    ``_candidate_score``），因此与首页合拍度同源、同配置。
    """
    row = await _profile_row(db, user_id)
    if row is None:
        raise HTTPException(404, detail="资料不存在，请先完成注册")
    if not row.get("mbti"):
        return EmotionLabMatchesResponse(items=[], hint="完成 MBTI 测试后才能看到合拍的人")
    ranked = await _rank_matches(db, user_id, limit)
    return EmotionLabMatchesResponse(
        items=[EmotionLabMatchItem(**item) for item in ranked],
        hint=None if ranked else "暂时没有可推荐的合拍用户",
    )
