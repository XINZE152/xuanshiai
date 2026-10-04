"""D-3 回归：AI 军师知识版本统一与启动期一致性校验。"""

from __future__ import annotations

import pathlib

import pytest

from app.core.config import settings
from app.services.ai_advisor import verify_knowledge_version

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = "seed-v2"


class _FakeResult:
    def __init__(self, rows: list[tuple[object]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object]]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[tuple[object]]) -> None:
        self._rows = rows

    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        return _FakeResult(self._rows)


class _BrokenSession:
    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        raise RuntimeError("knowledge table missing")


def test_config_default_matches_seed_script() -> None:
    assert settings.ai_advisor_knowledge_version == VERSION
    script = (ROOT / "scripts/seed_ai_advisor_knowledge.py").read_text(encoding="utf-8")
    assert f"version='{VERSION}'" in script or f"'{VERSION}'" in script


def test_schema_default_matches_config() -> None:
    source = (ROOT / "database_setup_marriage.py").read_text(encoding="utf-8")
    assert f"`version` varchar(64) NOT NULL DEFAULT '{VERSION}'" in source


def test_api_document_matches_config() -> None:
    doc = (ROOT / "docs/api/AI军师.md").read_text(encoding="utf-8")
    assert f"AI_ADVISOR_KNOWLEDGE_VERSION={VERSION}" in doc


@pytest.mark.asyncio
async def test_consistent_versions_pass() -> None:
    db = _FakeSession([(VERSION,)])
    assert await verify_knowledge_version(db) is None


@pytest.mark.asyncio
async def test_mismatched_versions_fail_fast_outside_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    db = _FakeSession([("seed-v1",)])
    with pytest.raises(RuntimeError, match="知识版本不一致"):
        await verify_knowledge_version(db)


@pytest.mark.asyncio
async def test_mismatched_versions_only_warn_in_test_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "testing")
    db = _FakeSession([("seed-v1",)])
    detail = await verify_knowledge_version(db)
    assert detail is not None and "知识版本不一致" in detail


@pytest.mark.asyncio
async def test_mixed_versions_are_treated_as_inconsistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "staging")
    db = _FakeSession([("seed-v1",), (VERSION,)])
    with pytest.raises(RuntimeError):
        await verify_knowledge_version(db)


@pytest.mark.asyncio
async def test_empty_knowledge_table_does_not_block_startup() -> None:
    assert await verify_knowledge_version(_FakeSession([])) is None


@pytest.mark.asyncio
async def test_unreadable_knowledge_table_does_not_block_startup() -> None:
    assert await verify_knowledge_version(_BrokenSession()) is None
