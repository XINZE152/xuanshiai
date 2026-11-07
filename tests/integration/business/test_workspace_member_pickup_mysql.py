"""MEMBER-PICKUP-20261107: two concurrent claims against isolated loopback MySQL."""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.services.matchmaker_workspace import WorkspaceActor, pickup_workspace_member


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_PICKUP_MYSQL_TEST") != "1", reason="requires opt-in disposable local MySQL database")
async def test_two_matchmakers_claim_once_in_disposable_mysql():
    url = make_url(settings.database_url)
    assert url.host in {"127.0.0.1", "localhost"}, "only an explicitly local MySQL server is allowed"
    name = "xsa_pickup_test_" + uuid.uuid4().hex
    assert name.startswith("xsa_pickup_test_") and name.replace("_", "").isalnum()
    admin = create_async_engine(url.set(database=None))
    isolated = create_async_engine(url.set(database=name))
    created = False
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
        created = True
        async with isolated.begin() as conn:
            await conn.execute(text("CREATE TABLE users (id BIGINT PRIMARY KEY) ENGINE=InnoDB"))
            await conn.execute(text("CREATE TABLE matchmaker_member_review (user_id BIGINT PRIMARY KEY, status VARCHAR(20), reason VARCHAR(255), reviewed_by BIGINT, reviewed_at DATETIME, updated_at DATETIME) ENGINE=InnoDB"))
            await conn.execute(text("CREATE TABLE resource_assignment (id BIGINT PRIMARY KEY AUTO_INCREMENT, user_id BIGINT NOT NULL, organization_id BIGINT, matchmaker_id BIGINT, source VARCHAR(32), status INT, assigned_by BIGINT, ended_at DATETIME, end_reason VARCHAR(255), KEY ix_user_status (user_id,status)) ENGINE=InnoDB"))
            await conn.execute(text("CREATE TABLE business_audit_log (id BIGINT PRIMARY KEY AUTO_INCREMENT, actor_user_id BIGINT, action VARCHAR(128), resource_type VARCHAR(64), resource_id BIGINT, reason VARCHAR(255)) ENGINE=InnoDB"))
            await conn.execute(text("INSERT INTO users VALUES (1)"))
            await conn.execute(text("INSERT INTO matchmaker_member_review(user_id,status,reason) VALUES (1,'REJECTED','test')"))
            await conn.execute(text("INSERT INTO resource_assignment(user_id,organization_id,matchmaker_id,source,status,assigned_by) VALUES (1,10,101,'manual',1,101)"))

        async def claim(actor_id):
            actor = WorkspaceActor(user_id=actor_id, display_name="测试红娘", level="NORMAL", organization_id=10)
            async with AsyncSession(isolated, expire_on_commit=False) as db:
                try:
                    result = await pickup_workspace_member(db, actor, 1)
                    return (200, result.matchmaker_id)
                except HTTPException as exc:
                    await db.rollback()
                    return (exc.status_code, actor_id)

        outcomes = await asyncio.gather(claim(201), claim(202))
        assert sorted(status for status, _ in outcomes) == [200, 409]
        winner = next(actor for status, actor in outcomes if status == 200)
        async with isolated.connect() as conn:
            active = (await conn.execute(text("SELECT matchmaker_id FROM resource_assignment WHERE user_id=1 AND status=1"))).scalars().all()
            status = (await conn.execute(text("SELECT status FROM matchmaker_member_review WHERE user_id=1"))).scalar_one()
            count = (await conn.execute(text("SELECT COUNT(*) FROM business_audit_log WHERE action='matchmaker_workspace.member.pickup'"))).scalar_one()
        assert active == [winner]
        assert status == "PENDING" and count == 1
    finally:
        await isolated.dispose()
        if created:
            async with admin.begin() as conn:
                await conn.execute(text(f"DROP DATABASE `{name}`"))
        await admin.dispose()
