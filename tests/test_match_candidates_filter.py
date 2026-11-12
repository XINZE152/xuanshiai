"""MATCH-FILTER-20261112: candidate preferences and explicit overrides."""
import json
import sqlite3

import pytest

from app.services import matchmaker_workspace as workspace


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def mappings(self):
        return self

    def all(self):
        return [dict(row) for row in self.cursor.fetchall()]

    def first(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None


class Database:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.create_function('JSON_LENGTH', 1, lambda value: len(json.loads(value)) if value else None)
        self.db.create_function('JSON_QUOTE', 1, json.dumps)
        self.db.create_function('JSON_CONTAINS', 2, lambda values, value: int(json.loads(value) in json.loads(values)) if values and value else 0)
        self.db.create_function('CURDATE', 0, lambda: '2026-11-12')
        self.db.create_function('TIMESTAMPDIFF', 3, lambda unit, birthday, today: int(today[:4]) - int(birthday[:4]) if birthday else None)
        self.db.execute("CREATE TABLE users(id INTEGER, nickname TEXT, avatar TEXT, gender INTEGER, birthday TEXT, is_married INTEGER, status INTEGER, deleted_at TEXT)")
        self.db.executescript('''
          CREATE TABLE user_profile(user_id INTEGER, residence TEXT, height INTEGER, income REAL, education_level INTEGER, residence_province_code TEXT, residence_city_code TEXT);
          CREATE TABLE user_auth(user_id INTEGER, education_verified INTEGER, education TEXT);
          CREATE TABLE user_privacy(user_id INTEGER, match_status INTEGER);
          CREATE TABLE user_partner_preference(user_id INTEGER, age_min INTEGER, age_max INTEGER, height_min INTEGER, height_max INTEGER, education_min INTEGER, income_min REAL, marriage_status INTEGER, preferred_province_code TEXT, preferred_city_codes TEXT);
          CREATE TABLE matchmaker_member_review(user_id INTEGER, status TEXT);
          CREATE TABLE resource_assignment(id INTEGER, user_id INTEGER, matchmaker_id INTEGER, status INTEGER);
          CREATE TABLE matchmaker_workspace_profile(user_id INTEGER, status INTEGER, display_name TEXT);
          INSERT INTO users VALUES (1,'目标',NULL,1,'1990-01-01',1,1,NULL),(2,'符合',NULL,2,'2000-01-01',1,1,NULL),(3,'年龄不符',NULL,2,'1980-01-01',1,1,NULL),(4,'已脱单',NULL,2,'2000-01-01',1,1,NULL);
          INSERT INTO user_profile(user_id,height) VALUES (2,165),(3,165),(4,165);
          INSERT INTO user_privacy VALUES (4,5);
          INSERT INTO matchmaker_member_review VALUES (2,'PASSED'),(3,'PASSED'),(4,'PASSED');
          INSERT INTO user_partner_preference(user_id,age_min,age_max) VALUES (1,20,30);
        ''')

    def sql(self, query):
        return str(query).replace('TIMESTAMPDIFF(YEAR,', "TIMESTAMPDIFF('YEAR',")

    async def execute(self, query, params):
        return Result(self.db.execute(self.sql(query), params))

    async def scalar(self, query, params):
        row = self.db.execute(self.sql(query), params).fetchone()
        return row[0] if row else None


@pytest.mark.asyncio
async def test_preference_override_and_pagination(monkeypatch):
    async def allow(*args):
        return None
    monkeypatch.setattr(workspace, '_require_scoped_member', allow)
    db = Database()
    actor = workspace.WorkspaceActor(user_id=1, display_name='总店', level='SUPER', organization_id=None)
    original = await workspace.list_match_candidates(db, actor, 1, 1, 1)
    assert original.total == 1 and [row.user_id for row in original.items] == [2]
    all_ages = await workspace.list_match_candidates(db, actor, 1, 1, 1, '{"ageRange":""}')
    assert all_ages.total == 2 and all_ages.has_more
    next_page = await workspace.list_match_candidates(db, actor, 1, 2, 1, '{"ageRange":""}')
    assert [row.user_id for row in next_page.items] == [2] and not next_page.has_more
    constrained = await workspace.list_match_candidates(db, actor, 1, 1, 10, '{"ageRange":"range:40:50"}')
    assert [row.user_id for row in constrained.items] == [3]


@pytest.mark.asyncio
async def test_candidates_include_other_matchmakers_and_unassigned(monkeypatch):
    async def allow(*args):
        return None
    monkeypatch.setattr(workspace, '_require_scoped_member', allow)
    db = Database()
    db.db.executescript("""
        INSERT INTO resource_assignment VALUES (1,2,999,1);
        INSERT INTO users VALUES (5,'待审核',NULL,2,'2000-01-01',1,1,NULL);
        INSERT INTO user_profile(user_id,height) VALUES (5,165);
        INSERT INTO matchmaker_member_review VALUES (5,'PENDING');
    """)
    normal = workspace.WorkspaceActor(user_id=101, display_name='服务红娘', level='NORMAL', organization_id=10)
    supervisor = workspace.WorkspaceActor(user_id=102, display_name='总店', level='SUPER', organization_id=None)
    for actor in (normal, supervisor):
        result = await workspace.list_match_candidates(db, actor, 1, 1, 10, '{"ageRange":""}')
        assert {row.user_id for row in result.items} == {2, 3}
