"""Isolated MySQL round trip; never connects to the remote server or existing user data."""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.schemas.matchmaker_workspace import WorkspaceMeetingDirectCreate, WorkspaceMeetingRecordStatusUpdate
from app.services import matchmaker_workspace as workspace


@pytest.mark.asyncio
async def test_real_mysql_meeting_round_trip(monkeypatch):
    raw_url = os.environ.get("WORKSPACE_MEETING_MYSQL_URL")
    if not raw_url:
        pytest.skip("WORKSPACE_MEETING_MYSQL_URL is required for isolated MySQL verification")
    url = make_url(raw_url)
    assert url.host in {"127.0.0.1", "localhost"}, "Only a local disposable MySQL server is allowed"
    name = "xsa_date_test_" + uuid.uuid4().hex[:16]
    assert name.startswith("xsa_date_test_") and name.replace("_", "").isalnum()
    admin_engine = create_async_engine(url)
    test_engine = create_async_engine(url.set(database=name))
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
        async with test_engine.begin() as connection:
            definitions = {
                "users": "id BIGINT PRIMARY KEY, nickname VARCHAR(128), avatar VARCHAR(255), gender INT, status INT DEFAULT 1",
                "resource_assignment": "id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT, matchmaker_id BIGINT, organization_id BIGINT, status INT DEFAULT 1",
                "matchmaker_workspace_profile": "user_id BIGINT PRIMARY KEY, display_name VARCHAR(128), status INT DEFAULT 1",
                "user_membership": "id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT, status INT, start_at DATETIME, end_at DATETIME",
                "meeting_request": "id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT, target_user_id BIGINT, matchmaker_id BIGINT, organization_id BIGINT, status VARCHAR(32) DEFAULT 'SUBMITTED', note VARCHAR(2000), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP",
                "meeting_record": "id BIGINT AUTO_INCREMENT PRIMARY KEY, request_id BIGINT, organizer_id BIGINT, organization_id BIGINT, scheduled_at DATETIME, location VARCHAR(255), status VARCHAR(32) DEFAULT 'SCHEDULED', cancel_reason VARCHAR(255), updated_at DATETIME DEFAULT CURRENT_TIMESTAMP",
                "member_follow_up": "id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT, method VARCHAR(32), content VARCHAR(2000), created_by BIGINT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
                "business_audit_log": "id BIGINT AUTO_INCREMENT PRIMARY KEY, actor_user_id BIGINT, action VARCHAR(128), resource_type VARCHAR(64), resource_id BIGINT, reason VARCHAR(255), after_json JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP",
            }
            for table, columns in definitions.items():
                await connection.execute(text(f"CREATE TABLE `{table}` ({columns}) ENGINE=InnoDB"))
            await connection.execute(text("INSERT INTO users (id, nickname, gender) VALUES (21, '测试红娘', 2), (62, '申请人', 1), (63, '被邀请方', 2)"))
            await connection.execute(text("INSERT INTO resource_assignment (user_id, matchmaker_id, organization_id) VALUES (62, 21, 1), (63, 21, 1)"))
            await connection.execute(text("INSERT INTO matchmaker_workspace_profile (user_id, display_name) VALUES (21, '测试红娘')"))

        # 交互限制有独立单测；本测试验证真实 SQL/类型/事务/序列化，不读取其它业务表。
        async def allow_interaction(*args):
            return None
        monkeypatch.setattr(workspace, "_ensure_meeting_users_can_interact", allow_interaction)
        actor = workspace.WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)
        async with AsyncSession(test_engine, expire_on_commit=False) as db:
            created = await workspace.create_workspace_meeting(db, actor, WorkspaceMeetingDirectCreate(
                applicant_user_id=62, target_user_id=63, scheduled_at="2030-11-04T18:00:00+08:00", location="测试门店",
                contacts_confirmed=True, applicant_agreed=True, target_agreed=True,
            ))
            page = await workspace.list_workspace_meeting_requests(db, actor, 1, 100, "申请人")
            assert page.total == 1 and page.workflow_version == 2
            assert page.items[0].meeting.id == created.id
            assert page.items[0].status == "ACCEPTED"
            assert page.items[0].meeting.scheduled_at.hour == 18
            assert page.items[0].meeting.location == "测试门店"
            assert page.items[0].meeting.follow_up_content is None
            await db.execute(text("UPDATE meeting_record SET scheduled_at = '2000-01-01 18:00:00' WHERE id = :id"), {"id": created.id})
            await db.commit()
            await workspace.update_workspace_meeting_record_status(db, actor, created.id, WorkspaceMeetingRecordStatusUpdate(
                status="COMPLETED", follow_up_content="双方已经见面，后续继续了解",
            ))
            page = await workspace.list_workspace_meeting_requests(db, actor, 1, 100, None)
            assert page.items[0].meeting.status == "COMPLETED"
            assert page.items[0].meeting.follow_up_content == "双方已经见面，后续继续了解"
            assert await db.scalar(text("SELECT COUNT(*) FROM member_follow_up")) == 1
            await workspace.update_workspace_meeting_record_status(db, actor, created.id, WorkspaceMeetingRecordStatusUpdate(
                status="COMPLETED", follow_up_content="重复提交",
            ))
            assert await db.scalar(text("SELECT COUNT(*) FROM member_follow_up")) == 1
            # 旧申请未写归属仍通过申请人当前归属可见，无需回填或迁移。
            await db.execute(text("UPDATE meeting_request SET matchmaker_id = NULL, organization_id = NULL"))
            page = await workspace.list_workspace_meeting_requests(db, actor, 1, 100, None)
            assert page.total == 1
            other = workspace.WorkspaceActor(user_id=22, display_name="其他红娘", level="NORMAL", organization_id=1)
            assert (await workspace.list_workspace_meeting_requests(db, other, 1, 100, None)).total == 0
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        await admin_engine.dispose()
