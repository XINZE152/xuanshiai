"""GOOD-NEWS-20261104: input, privacy, idempotency and transaction barriers."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.matchmaker_workspace import WorkspaceGoodNewsBlessingCreate, WorkspacePennantCreate
from app.services import workspace_good_news as service
from app.services.matchmaker_workspace import WorkspaceActor

ACTOR = WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)


def result(row=None, lastrowid=7):
    return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row), lastrowid=lastrowid)


@pytest.mark.parametrize("message", ["", "  ", "x" * 101])
def test_blessing_rejects_empty_or_long_message(message):
    with pytest.raises(ValidationError):
        WorkspaceGoodNewsBlessingCreate(message=message)


@pytest.mark.parametrize("changes", [
    {"consent_confirmed": False}, {"giver": " "}, {"left_words": " "},
    {"image_url": "https://other.test/not-an-upload.png"},
])
def test_pennant_requires_consent_and_valid_content(changes):
    with pytest.raises(ValidationError):
        WorkspacePennantCreate(**{
            "giver": "会员", "left_words": "感谢红娘", "right_words": "真诚服务", "consent_confirmed": True,
            "client_request_id": "test-key-123", **changes,
        })


@pytest.mark.asyncio
async def test_out_of_scope_blessing_is_404_before_any_write():
    db = SimpleNamespace(execute=AsyncMock(return_value=result()), scalar=AsyncMock(), commit=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await service.create_blessing(db, ACTOR, 8, WorkspaceGoodNewsBlessingCreate(message="送上祝福"))
    assert error.value.status_code == 404
    sql = str(db.execute.await_args.args[0])
    assert "$.matchmaker_id" in sql and "FOR UPDATE" in sql
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_news_cannot_receive_blessings():
    db = SimpleNamespace(execute=AsyncMock(return_value=result({"id": 8, "status": 2, "extra_json": "{}"})), scalar=AsyncMock(), commit=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await service.create_blessing(db, ACTOR, 8, WorkspaceGoodNewsBlessingCreate(message="送上祝福"))
    assert error.value.status_code == 409
    db.scalar.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_blessing_preserves_count_and_creates_nothing():
    db = SimpleNamespace(execute=AsyncMock(return_value=result({"id": 8, "status": 1, "extra_json": '{"blessing_count":4}'})), scalar=AsyncMock(return_value=9), commit=AsyncMock())
    response = await service.create_blessing(db, ACTOR, 8, WorkspaceGoodNewsBlessingCreate(message="送上祝福"))
    assert response.blessing_count == 4 and response.blessed and not response.created
    assert len(db.execute.await_args_list) == 1
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_blessing_record_and_counter_share_one_commit():
    db = SimpleNamespace(execute=AsyncMock(return_value=result({"id": 8, "status": 1, "extra_json": '{"blessing_count":4,"partner_name":"会员"}'})), scalar=AsyncMock(return_value=None), commit=AsyncMock())
    response = await service.create_blessing(db, ACTOR, 8, WorkspaceGoodNewsBlessingCreate(message=" 送上祝福 "))
    calls = db.execute.await_args_list
    assert "FOR UPDATE" in str(calls[0].args[0])
    assert "INSERT INTO admin_content_item" in str(calls[1].args[0])
    update = next(call for call in calls if "UPDATE admin_content_item" in str(call.args[0]))
    extra = json.loads(update.args[1]["extra"])
    assert extra["blessing_count"] == 5 and extra["partner_name"] == "会员"
    assert response.created and response.blessing_count == 5
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_blessing_insert_never_changes_counter():
    def execute(sql, params):
        if "INSERT INTO admin_content_item" in str(sql):
            raise RuntimeError("write failed")
        return result({"id": 8, "status": 1, "extra_json": "{}"})
    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), scalar=AsyncMock(return_value=None), commit=AsyncMock())
    with pytest.raises(RuntimeError):
        await service.create_blessing(db, ACTOR, 8, WorkspaceGoodNewsBlessingCreate(message="祝福你们"))
    assert not any("UPDATE admin_content_item" in str(call.args[0]) for call in db.execute.await_args_list)
    db.commit.assert_not_awaited()
