"""学历材料提交接入在线核验后的状态流转与人工兜底。"""

from __future__ import annotations

from typing import Any

import pytest

from app.services import certifications as service
from app.services.education.base import EducationVerifyResult
from app.services.education.providers import DisabledEducationProvider


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def scalar(self) -> Any:
        if not self._rows:
            return None
        row = self._rows[0]
        return row.get("real_name") if isinstance(row, dict) else row


class _Db:
    def __init__(self, *results: _Result) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rolled_back = False
        self.committed = False

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(statement), dict(params or {})))
        return self.results.pop(0) if self.results else _Result()

    async def rollback(self) -> None:
        self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True


class _Provider:
    name = "mock"

    def __init__(self, result: EducationVerifyResult | Exception) -> None:
        self.result = result

    async def verify(self, request: Any) -> EducationVerifyResult:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def _stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    async def store(_user_id: int, _prefix: str, _file: Any) -> str:
        return "/storage/uploads/7/education-cert-a.webp"

    async def certifications(_db: Any, _user_id: int) -> str:
        return "certs"

    monkeypatch.setattr(service, "_store_certification_image", store)
    monkeypatch.setattr(service, "get_certifications", certifications)


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    monkeypatch.setattr(service, "get_education_provider", lambda: provider)


@pytest.mark.asyncio
async def test_disabled_provider_keeps_manual_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, DisabledEducationProvider())
    db = _Db(_Result())

    result = await service.submit_education_material(db, 7, "本科", "南京大学", object())

    assert result == "certs"
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 1
    assert insert_params["reason"] is None
    assert db.rolled_back is False
    assert db.committed is True


@pytest.mark.asyncio
async def test_verified_provider_marks_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, _Provider(EducationVerifyResult(verified=True, provider="mock")))
    db = _Db(
        _Result([{"real_name": "张三"}]),
        _Result(),
    )

    result = await service.submit_education_material(db, 7, "本科", "南京大学", object())

    assert result == "certs"
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 2
    assert insert_params["reason"] is None
    assert db.rolled_back is True
    assert db.committed is True


@pytest.mark.asyncio
async def test_rejected_provider_marks_failed_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(
        monkeypatch,
        _Provider(EducationVerifyResult(verified=False, provider="mock", reason="report_mismatch")),
    )
    db = _Db(
        _Result([{"real_name": "张三"}]),
        _Result(),
    )

    result = await service.submit_education_material(db, 7, "本科", "南京大学", object())

    assert result == "certs"
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 3
    assert insert_params["reason"] == "report_mismatch"


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_provider(monkeypatch, _Provider(RuntimeError("vendor down")))
    db = _Db(
        _Result([{"real_name": "张三"}]),
        _Result(),
    )

    result = await service.submit_education_material(db, 7, "本科", "南京大学", object())

    assert result == "certs"
    insert_params = [params for sql, params in db.calls if "INSERT INTO user_auth" in sql][0]
    assert insert_params["status"] == 1
