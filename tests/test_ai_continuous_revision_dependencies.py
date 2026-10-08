"""双主体版本依赖仅对显式 continuous_v2 任务放宽。"""
from types import SimpleNamespace

import pytest

from app.services.ai.tasks import _task_revisions_match
from app.services.revisions import RevisionVector


@pytest.mark.parametrize("task_type,subject,ignored", [
    ("profile_preview", "personal", {"preference"}),
    ("profile_preview", "ideal_partner", {"profile"}),
    ("moxiang_candidate_extract", "personal", {"profile", "preference"}),
    ("profile_narrative", "personal", set()),
])
def test_continuous_dependency_isolation(task_type, subject, ignored):
    task = SimpleNamespace(task_type=task_type, payload_summary={"flow_version": "continuous_v2", "subject": subject})
    original = RevisionVector().as_dict()
    assert _task_revisions_match(task, original, original)
    for key in original:
        changed = {**original, key: original[key] + 1}
        assert _task_revisions_match(task, original, changed) == (key in ignored)
    assert not _task_revisions_match(task, {}, original)


def test_legacy_preview_preserves_full_vector_gate():
    task = SimpleNamespace(task_type="profile_preview", payload_summary={"subject": "personal"})
    original = RevisionVector().as_dict()
    assert not _task_revisions_match(task, original, {**original, "preference": 1})
