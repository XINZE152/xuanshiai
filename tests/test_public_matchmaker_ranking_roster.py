import asyncio

from sqlalchemy import create_engine, text

from app.services.matchmaker import get_matchmaker, list_matchmakers


def test_ranking_and_detail_use_staff_roster_and_completed_service_count():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for sql in (
            "CREATE TABLE users (id INTEGER, nickname TEXT, avatar TEXT, status INTEGER, deleted_at TEXT)",
            "CREATE TABLE user_matchmaker_apply (id INTEGER, user_id INTEGER, application_type TEXT, status INTEGER, intro TEXT, slogan TEXT, application_details TEXT, reviewed_at TEXT)",
            "CREATE TABLE matchmaker_profile (user_id INTEGER, deleted_at TEXT)",
            "CREATE TABLE user_role (user_id INTEGER, role_code TEXT, status INTEGER)",
            "CREATE TABLE matchmaker_service (matchmaker_id INTEGER, status INTEGER)",
            "CREATE TABLE matchmaker_rating (matchmaker_id INTEGER, score INTEGER)",
            "CREATE TABLE resource_assignment (id INTEGER, user_id INTEGER, matchmaker_id INTEGER, status INTEGER)",
            "CREATE TABLE meeting_request (id INTEGER, user_id INTEGER, target_user_id INTEGER)",
            "CREATE TABLE meeting_record (id INTEGER, request_id INTEGER, status TEXT)",
            "INSERT INTO users VALUES (1, 'A', NULL, 1, NULL), (2, 'B', NULL, 1, NULL), (3, 'C', NULL, 1, NULL), (10, 'M', NULL, 1, NULL), (11, 'N', NULL, 1, NULL), (12, 'D', NULL, 1, 'deleted')",
            "INSERT INTO user_matchmaker_apply VALUES (1, 1, 'service_matchmaker', 1, '', NULL, NULL, '2026-01-01'), (2, 2, 'service_matchmaker', 1, '', NULL, NULL, '2026-01-02'), (3, 3, 'service_matchmaker', 1, '', NULL, NULL, '2026-01-03')",
            "INSERT INTO matchmaker_profile VALUES (3, '2026-01-01')",
            "INSERT INTO user_role VALUES (1, 'service_matchmaker', 1)",
            "INSERT INTO matchmaker_service VALUES (2, 2), (2, 2), (1, 1)",
            "INSERT INTO resource_assignment VALUES (1,10,1,1),(2,10,2,1),(3,11,2,1),(4,12,2,1)",
            "INSERT INTO meeting_request VALUES (1,10,11),(2,10,11),(3,10,11)",
            "INSERT INTO meeting_record VALUES (1,1,'COMPLETED'),(2,2,'COMPLETED'),(3,3,'CANCELLED')",
        ):
            conn.execute(text(sql))

        class DB:
            async def execute(self, statement, params=None):
                return conn.execute(statement, params or {})

        async def check():
            ranking = await list_matchmakers(DB(), 1, 50, ranking=True)
            assert [(x.user_id, x.success_count, x.is_available) for x in ranking.items] == [(2, 2, False), (1, 0, True)]
            assert [(x.user_id, x.served_member_count, x.successful_meeting_member_count) for x in ranking.items] == [(2, 2, 2), (1, 0, 0)]
            assert ranking.total == 2
            detail = await get_matchmaker(DB(), 2)
            assert detail.success_count == 2
            assert not {"phone", "wechat", "username"} & set(detail.model_dump())

        asyncio.run(check())
