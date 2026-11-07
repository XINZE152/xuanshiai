"""REVIEW-FILTER-20261107: filtering must not escape scope or precede pagination."""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.services.matchmaker_workspace import WorkspaceActor, list_workspace_members


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class Database:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, nickname TEXT, avatar TEXT, gender INTEGER, birthday TEXT, is_married INTEGER, phone TEXT, created_at TEXT);
        CREATE TABLE user_profile(user_id INTEGER, hometown TEXT, residence TEXT, hometown_province_code TEXT, hometown_city_code TEXT, hometown_district_code TEXT, residence_province_code TEXT, residence_city_code TEXT, residence_district_code TEXT, household TEXT, household_province_code TEXT, household_city_code TEXT, household_district_code TEXT, education_level INTEGER, occupation TEXT, height INTEGER, weight INTEGER, income REAL, house TEXT, car TEXT, smoking TEXT, drinking TEXT, ethnicity TEXT, religion TEXT, mbti TEXT);
        CREATE TABLE user_auth(user_id INTEGER, education TEXT, education_verified INTEGER, job TEXT, realname_status INTEGER);
        CREATE TABLE user_profile_completion(user_id INTEGER, score REAL);
        CREATE TABLE matchmaker_member_review(user_id INTEGER, status TEXT, reason TEXT, reviewed_at TEXT, reviewed_by INTEGER, updated_at TEXT);
        CREATE TABLE member_follow_up(user_id INTEGER, next_follow_at TEXT);
        CREATE TABLE resource_assignment(id INTEGER PRIMARY KEY, user_id INTEGER, organization_id INTEGER, matchmaker_id INTEGER, status INTEGER, ended_at TEXT, end_reason TEXT, source TEXT, assigned_by INTEGER);
        CREATE TABLE matchmaker_workspace_profile(user_id INTEGER, status INTEGER, display_name TEXT);
        CREATE TABLE business_audit_log(actor_user_id INTEGER, action TEXT, resource_type TEXT, resource_id INTEGER, reason TEXT);
        INSERT INTO users(id,nickname,gender,created_at) VALUES (1,'A',2,'2026-01-01 00:00:00'),(2,'B',2,'2026-01-01 00:00:00'),(3,'C',1,'2026-01-01 00:00:00');
        INSERT INTO user_profile(user_id,occupation,height,education_level) VALUES (1,'教师',165,3),(2,'教师',165,3),(3,'教师',165,3);
        INSERT INTO resource_assignment(id,user_id,organization_id,matchmaker_id,status) VALUES (1,1,10,101,1),(2,2,20,202,1),(3,3,10,101,1);
        """)

    async def execute(self, query, params):
        sql = str(query).replace(' FOR UPDATE', '').replace('UTC_TIMESTAMP()', 'CURRENT_TIMESTAMP')
        cursor = self.db.execute(sql, params)
        return Result([dict(row) for row in cursor.fetchall()])

    async def scalar(self, query, params):
        return self.db.execute(str(query), params).fetchone()[0]

    async def commit(self):
        self.db.commit()


@pytest.mark.asyncio
async def test_filters_apply_before_pagination_and_preserve_two_matchmaker_scopes():
    db = Database()
    first = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    second = WorkspaceActor(user_id=202, display_name='B', level='NORMAL', organization_id=20)
    filters = json.dumps({'gender': 'female', 'occupation': '教师'}, ensure_ascii=False)
    one = await list_workspace_members(db, first, 1, 1, None, None, filters)
    two = await list_workspace_members(db, second, 1, 1, None, None, filters)
    assert [x.user_id for x in one.items] == [1] and one.total == 1
    assert [x.user_id for x in two.items] == [2] and two.total == 1
    org = WorkspaceActor(user_id=101, display_name='A', level='SUPER', organization_id=10)
    page = await list_workspace_members(db, org, 1, 1, None, None, json.dumps({'occupation': '教师'}, ensure_ascii=False))
    assert page.total == 2 and page.has_more and page.items[0].user_id == 3
    page2 = await list_workspace_members(db, org, 2, 1, None, None, json.dumps({'occupation': '教师'}, ensure_ascii=False))
    assert page2.items[0].user_id == 1 and not page2.has_more


@pytest.mark.asyncio
async def test_unknown_or_injected_filter_is_rejected():
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    with pytest.raises(HTTPException) as error:
        await list_workspace_members(Database(), actor, 1, 20, None, None, '{"occupation":"教师\u0027 OR 1=1 --"}')
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_region_and_profile_filters_keep_scope():
    db = Database()
    db.db.execute("UPDATE user_profile SET residence_province_code='330000', residence_city_code='330100', residence_district_code='330106', weight=55, mbti='INTJ' WHERE user_id IN (1,2)")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    filters = json.dumps({'residence': '浙江省 杭州市 西湖区', 'weight': '55', 'mbti': 'INTJ'}, ensure_ascii=False)
    page = await list_workspace_members(db, actor, 1, 20, None, None, filters)
    assert [row.user_id for row in page.items] == [1]
    assert page.filter_version == 1


@pytest.mark.asyncio
async def test_workbench_route_forwards_filter_to_scoped_query(monkeypatch):
    from app.api.routes import matchmaker_workspace as route

    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)

    async def authorized_actor(db, current):
        return actor

    monkeypatch.setattr(route, 'require_workspace', authorized_actor)
    page = await route.workbench_members(page=1, page_size=20, review_status=None, search=None,
                                        filters=json.dumps({'gender': 'female'}), current=object(), db=Database())
    assert [row.user_id for row in page.items] == [1]
    assert page.filter_version == 1


@pytest.mark.asyncio
async def test_education_filter_matches_verified_display_and_mine_cannot_broaden_scope():
    db = Database()
    db.db.execute("INSERT INTO user_auth(user_id,education,education_verified) VALUES (1,'硕士',1)")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    page = await list_workspace_members(db, actor, 1, 20, None, None, json.dumps({'education': 'masters', 'mine': '1'}))
    assert [row.user_id for row in page.items] == [1]
    page = await list_workspace_members(db, actor, 1, 20, None, None, json.dumps({'education': 'bachelor'}))
    assert [row.user_id for row in page.items] == [3]


@pytest.mark.asyncio
async def test_rejected_member_pickup_reassigns_and_stays_within_organization():
    from app.services.matchmaker_workspace import pickup_workspace_member

    db = Database()
    db.db.execute("INSERT INTO matchmaker_member_review(user_id,status,reason) VALUES (1,'REJECTED','资料不完整'),(3,'REJECTED','资料不完整')")
    actor = WorkspaceActor(user_id=202, display_name='B', level='NORMAL', organization_id=10)
    before = await list_workspace_members(db, actor, 1, 20, 'REJECTED', None)
    assert {row.user_id for row in before.items} == {1, 3}
    result = await pickup_workspace_member(db, actor, 1)
    assert result.member_id == 1 and result.matchmaker_id == 202 and result.review_status == 'PENDING'
    assignment = db.db.execute('SELECT matchmaker_id, organization_id FROM resource_assignment WHERE user_id=1 AND status=1').fetchall()
    assert [tuple(row) for row in assignment] == [(202, 10)]
    assert db.db.execute('SELECT status FROM matchmaker_member_review WHERE user_id=1').fetchone()[0] == 'PENDING'
    assert db.db.execute("SELECT COUNT(*) FROM business_audit_log WHERE action='matchmaker_workspace.member.pickup'").fetchone()[0] == 1
    with pytest.raises(HTTPException) as error:
        await pickup_workspace_member(db, actor, 1)
    assert error.value.status_code == 409
    outsider = WorkspaceActor(user_id=303, display_name='C', level='NORMAL', organization_id=20)
    with pytest.raises(HTTPException) as error:
        await pickup_workspace_member(db, outsider, 3)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_custom_bounds_filter_before_pagination_and_reject_reverse_bounds():
    db = Database()
    db.db.execute("UPDATE user_profile SET height=160, weight=50 WHERE user_id=1")
    db.db.execute("UPDATE user_profile SET height=170, weight=60 WHERE user_id=3")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    page = await list_workspace_members(db, actor, 1, 1, None, None,
        json.dumps({'heightRange': 'range:160:165', 'weight': 'range::50'}))
    assert [row.user_id for row in page.items] == [1]
    assert page.total == 1 and not page.has_more
    with pytest.raises(HTTPException) as error:
        await list_workspace_members(db, actor, 1, 20, None, None,
            json.dumps({'heightRange': 'range:180:160'}))
    assert error.value.status_code == 422

@pytest.mark.parametrize(('value', 'expected'), [
    ('under40', 'p.weight < 40'),
    ('40-49', 'p.weight >= 40 AND p.weight < 50'),
    ('50-59', 'p.weight >= 50 AND p.weight < 60'),
    ('60-69', 'p.weight >= 60 AND p.weight < 70'),
    ('70-79', 'p.weight >= 70 AND p.weight < 80'),
    ('over80', 'p.weight >= 80'),
])
def test_weight_ranges_include_their_lower_bound_and_exclude_the_next(value, expected):
    from app.services.matchmaker_workspace import _append_workspace_member_filters

    where, params = [], {}
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    _append_workspace_member_filters(where, params, actor, json.dumps({'weight': value}))
    assert where == [f'({expected})']
    assert params == {}
