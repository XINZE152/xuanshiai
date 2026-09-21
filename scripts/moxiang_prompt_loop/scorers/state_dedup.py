"""会话状态与去重评分。

校验项:
- ``ai_task`` 同一 ``(task_type, idempotency_key)`` 仅一条(无重复生成)
- ``ai_profile_candidate.content_hash`` 在同 ``(session_id, content_hash)``
  仅一条
- ``ai_profile_draft.expected_revision`` 单调递增
- 拒绝/撤回的字段不会被 promote 进 draft

阈值: 全部通过才计 100;任一不通过扣 25。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from scripts.moxiang_prompt_loop import db_snapshot
from scripts.moxiang_prompt_loop._score_types import ScoreReport

THRESHOLD = 100.0


def score(run_meta: dict[str, Any], snapshot: db_snapshot.Snapshot) -> ScoreReport:
    tasks = snapshot.tables.get("ai_task", [])
    candidates = snapshot.tables.get("ai_profile_candidate", [])
    drafts = snapshot.tables.get("ai_profile_draft", [])

    checks: list[dict[str, Any]] = []

    # 1. ai_task 去重
    task_keys = Counter(
        (t.get("task_type"), t.get("idempotency_key"))
        for t in tasks
        if t.get("task_type") and t.get("idempotency_key")
    )
    task_dup = {k: v for k, v in task_keys.items() if v > 1}
    checks.append(
        {
            "rule": "ai_task_no_duplicate",
            "violations": [{"key": list(k), "count": c} for k, c in task_dup.items()],
            "passed": not task_dup,
        }
    )

    # 2. ai_profile_candidate content_hash 唯一
    cand_keys = Counter(
        (c.get("session_id"), c.get("content_hash"))
        for c in candidates
        if c.get("session_id") and c.get("content_hash")
    )
    cand_dup = {k: v for k, v in cand_keys.items() if v > 1}
    checks.append(
        {
            "rule": "ai_profile_candidate_unique_content_hash",
            "violations": [{"key": [str(s) for s in k], "count": c} for k, c in cand_dup.items()],
            "passed": not cand_dup,
        }
    )

    # 3. ai_profile_draft.expected_revision 单调递增(同一 user 同一 subject)
    draft_seq = sorted(
        drafts,
        key=lambda d: (
            d.get("user_id", 0),
            d.get("subject", ""),
            d.get("expected_revision", 0),
        ),
    )
    monotonic = True
    last = -1
    for d in draft_seq:
        rev = int(d.get("expected_revision") or 0)
        if rev < last:
            monotonic = False
            break
        last = rev
    checks.append(
        {
            "rule": "ai_profile_draft_monotonic_revision",
            "draft_count": len(draft_seq),
            "passed": monotonic,
        }
    )

    # 4. 拒绝/撤回字段不会被 promote(此处只检查 draft 字段没有
    # confirmation_status='rejected' 出现)
    draft_fields = snapshot.tables.get("ai_profile_draft_field", [])
    rejected_in_draft = [
        f.get("field_key")
        for f in draft_fields
        if (f.get("confirmation_status") or "").lower() == "rejected"
    ]
    checks.append(
        {
            "rule": "rejected_fields_not_in_draft",
            "rejected_field_keys": rejected_in_draft,
            "passed": not rejected_in_draft,
        }
    )

    failures = [c for c in checks if not c["passed"]]
    score_value = max(THRESHOLD - 25 * len(failures), 0.0)

    return ScoreReport(
        name="state_dedup",
        score=float(score_value),
        threshold=THRESHOLD,
        passed=not failures,
        details={"checks": checks, "failure_count": len(failures)},
    )
