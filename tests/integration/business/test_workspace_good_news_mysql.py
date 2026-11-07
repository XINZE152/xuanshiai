"""GOOD-NEWS-20261104: isolated loopback MySQL flow; never writes existing user data."""
import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.schemas.matchmaker_workspace import WorkspaceGoodNewsBlessingCreate, WorkspaceGoodNewsCreate, WorkspacePennantCreate
from app.services import workspace_good_news as news
from app.services.matchmaker_workspace import WorkspaceActor, create_workbench_good_news, list_workbench_good_news_pennants


@pytest.mark.asyncio
async def test_good_news_round_trip_and_concurrent_blessing():
    raw_url = os.environ.get("WORKSPACE_MEETING_MYSQL_URL")
    if not raw_url:
        pytest.skip("Set WORKSPACE_MEETING_MYSQL_URL to a disposable local MySQL server")
    url = make_url(raw_url)
    assert url.host in {"127.0.0.1", "localhost"}
    name = "xsa_news_test_" + uuid.uuid4().hex[:16]
    assert name.replace("_", "").isalnum()
    admin = create_async_engine(url)
    engine = create_async_engine(url.set(database=name))
    actor = WorkspaceActor(user_id=21, display_name="测试红娘", level="NORMAL", organization_id=1)
    other = WorkspaceActor(user_id=22, display_name="另一红娘", level="NORMAL", organization_id=1)
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"))
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE users (id BIGINT PRIMARY KEY, nickname VARCHAR(64), avatar VARCHAR(255)) ENGINE=InnoDB"))
            await connection.execute(text("INSERT INTO users VALUES (21,'测试红娘',NULL),(22,'另一红娘',NULL)"))
            await connection.execute(text("""CREATE TABLE admin_content_item (
                id BIGINT AUTO_INCREMENT PRIMARY KEY, tenant_id BIGINT, domain VARCHAR(64), title VARCHAR(128),
                subtitle VARCHAR(255), image_url VARCHAR(1024), status INT, sort INT DEFAULT 0, extra_json LONGTEXT,
                created_by BIGINT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                KEY idx_content_domain (tenant_id,domain,status,sort)) ENGINE=InnoDB"""))
            await connection.execute(text("""CREATE TABLE business_audit_log (id BIGINT AUTO_INCREMENT PRIMARY KEY,
                actor_user_id BIGINT, action VARCHAR(128), resource_type VARCHAR(64), resource_id BIGINT, reason VARCHAR(255)) ENGINE=InnoDB"""))
        async with AsyncSession(engine, expire_on_commit=False) as db:
            body = WorkspaceGoodNewsCreate(stage="牵手成功", partner_name="林女士", feedback_name="李先生", content="双方愿意继续了解", consent_confirmed=True, client_request_id="local-news-key-1")
            created = await create_workbench_good_news(db, actor, body)
            repeated = await create_workbench_good_news(db, actor, body)
            assert repeated.id == created.id
            assert await db.scalar(text("SELECT COUNT(*) FROM admin_content_item WHERE domain='good_news'")) == 1
            detail = await news.good_news_detail(db, actor, created.id)
            assert detail.from_name == "李先生" and detail.pending_review
            with pytest.raises(HTTPException) as error:
                await news.good_news_detail(db, other, created.id)
            assert error.value.status_code == 404
            with pytest.raises(HTTPException) as error:
                await news.create_blessing(db, actor, created.id, WorkspaceGoodNewsBlessingCreate(message="送上祝福"))
            assert error.value.status_code == 409
            await db.execute(text("UPDATE admin_content_item SET status=1 WHERE id=:id"), {"id": created.id})
            await db.commit()
        async def send():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await news.create_blessing(session, actor, created.id, WorkspaceGoodNewsBlessingCreate(message="祝福你们，彼此珍惜"))
        first, second = await asyncio.gather(send(), send())
        assert sum(result.created for result in (first, second)) == 1
        assert first.blessing_count == second.blessing_count == 1
        async with AsyncSession(engine, expire_on_commit=False) as db:
            blessings = await news.list_blessings(db, actor, created.id, 1, 20)
            assert blessings.total == 1 and blessings.blessed
            assert blessings.items[0].message == "祝福你们，彼此珍惜"
            assert (await news.good_news_detail(db, actor, created.id)).blessing_count == 1
            gift = WorkspacePennantCreate(giver="李先生", left_words="感谢红娘", right_words="真诚服务", consent_confirmed=True, client_request_id="gift-test-key-1")
            pennant = await news.create_pennant(db, actor, gift)
            assert (await news.create_pennant(db, actor, gift)).id == pennant.id
            detail = await news.pennant_detail(db, actor, pennant.id)
            assert detail.target_user_id == 21 and detail.pending_review
            renamed = WorkspaceActor(user_id=21, display_name="已经改名的红娘", level="NORMAL", organization_id=1)
            assert (await list_workbench_good_news_pennants(db, renamed, 1, 20)).total == 1
            assert (await list_workbench_good_news_pennants(db, other, 1, 20)).total == 0
            with pytest.raises(HTTPException) as error:
                await news.create_blessing(db, actor, pennant.id, WorkspaceGoodNewsBlessingCreate(message="不同域不能混用"))
            assert error.value.status_code == 404
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        await admin.dispose()
