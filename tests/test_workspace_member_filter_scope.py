"""REVIEW-FILTER-20261107: filtering must not escape scope or precede pagination."""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.services.matchmaker_workspace import WorkspaceActor, list_workspace_members, abandon_workspace_member, pickup_workspace_abandoned_member


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
        self.db.create_function('CONCAT', -1, lambda *parts: ''.join(str(part) for part in parts))
        self.db.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, nickname TEXT, avatar TEXT, gender INTEGER, birthday TEXT, is_married INTEGER, phone TEXT, created_at TEXT, deleted_at TEXT);
        CREATE TABLE member_abandonment(id INTEGER PRIMARY KEY, user_id INTEGER, reason TEXT, abandon_type TEXT, abandoned_by INTEGER, restored_at TEXT, restored_by INTEGER, restore_reason TEXT, assigned_matchmaker_id INTEGER);
        CREATE TABLE matchmaker_admin_account(id INTEGER, matchmaker_user_id INTEGER, status INTEGER);
        CREATE TABLE user_membership(id INTEGER, user_id INTEGER, status INTEGER);
        CREATE TABLE offline_vip(id INTEGER, user_id INTEGER, deleted_at TEXT);
        CREATE TABLE user_profile(user_id INTEGER, hometown TEXT, residence TEXT, hometown_province_code TEXT, hometown_city_code TEXT, hometown_district_code TEXT, residence_province_code TEXT, residence_city_code TEXT, residence_district_code TEXT, household TEXT, household_province_code TEXT, household_city_code TEXT, household_district_code TEXT, education_level INTEGER, occupation TEXT, height INTEGER, weight INTEGER, income REAL, house TEXT, car TEXT, smoking TEXT, drinking TEXT, ethnicity TEXT, religion TEXT, mbti TEXT);
        CREATE TABLE user_auth(user_id INTEGER, education TEXT, education_verified INTEGER, job TEXT, realname_status INTEGER, real_name TEXT);
        CREATE TABLE user_profile_completion(user_id INTEGER, score REAL);
        CREATE TABLE matchmaker_member_review(user_id INTEGER, status TEXT, reason TEXT, reviewed_at TEXT, reviewed_by INTEGER, updated_at TEXT);
        CREATE TABLE member_follow_up(user_id INTEGER, next_follow_at TEXT);
        CREATE TABLE resource_assignment(id INTEGER PRIMARY KEY, user_id INTEGER, organization_id INTEGER, matchmaker_id INTEGER, status INTEGER, ended_at TEXT, end_reason TEXT, source TEXT, assigned_by INTEGER);
        CREATE TABLE matchmaker_workspace_profile(user_id INTEGER, status INTEGER, display_name TEXT);
        CREATE TABLE business_audit_log(actor_user_id INTEGER, action TEXT, resource_type TEXT, resource_id INTEGER, reason TEXT);
        INSERT INTO users(id,nickname,gender,created_at) VALUES (1,'A',2,'2026-01-01 00:00:00'),(2,'B',2,'2026-01-01 00:00:00'),(3,'C',1,'2026-01-01 00:00:00');
        INSERT INTO user_profile(user_id,occupation,height,education_level) VALUES (1,'教师',165,3),(2,'教师',165,3),(3,'教师',165,3);
        INSERT INTO resource_assignment(id,user_id,organization_id,matchmaker_id,status) VALUES (1,1,10,101,1),(2,2,20,202,1),(3,3,10,101,1);
        INSERT INTO matchmaker_admin_account(id,matchmaker_user_id,status) VALUES (11,101,1),(22,202,1);
        """)

    async def execute(self, query, params):
        sql = str(query).replace(' FOR UPDATE', '').replace('UTC_TIMESTAMP()', 'CURRENT_TIMESTAMP')
        cursor = self.db.execute(sql, params)
        return Result([dict(row) for row in cursor.fetchall()])

    async def scalar(self, query, params):
        row = self.db.execute(str(query).replace(' FOR UPDATE', ''), params).fetchone()
        return row[0] if row is not None else None

    async def commit(self):
        self.db.commit()


@pytest.mark.asyncio
async def test_abandon_and_pickup_share_crm_pool_and_preserve_review():
    db = Database()
    db.db.execute("INSERT INTO matchmaker_member_review(user_id,status) VALUES (1,'PASSED')")
    supervisor = WorkspaceActor(user_id=101, display_name='A', level='SUPER', organization_id=None)
    await abandon_workspace_member(db, supervisor, 1, '测试原因')
    row = db.db.execute('SELECT user_id, reason, abandoned_by, restored_at FROM member_abandonment').fetchone()
    assert tuple(row) == (1, '测试原因', 11, None)
    assert db.db.execute('SELECT status FROM matchmaker_member_review WHERE user_id=1').fetchone()[0] == 'PASSED'
    with pytest.raises(HTTPException) as duplicate:
        await abandon_workspace_member(db, supervisor, 1, '重复')
    assert duplicate.value.status_code == 409
    wrong_org = WorkspaceActor(user_id=202, display_name='B', level='NORMAL', organization_id=20)
    with pytest.raises(HTTPException) as denied:
        await pickup_workspace_abandoned_member(db, wrong_org, 1)
    assert denied.value.status_code == 404
    await pickup_workspace_abandoned_member(db, supervisor, 1)
    row = db.db.execute('SELECT restored_by, restored_at, assigned_matchmaker_id FROM member_abandonment').fetchone()
    assert row[0] == 11 and row[1] is not None and row[2] == 101
    assert db.db.execute('SELECT status FROM matchmaker_member_review WHERE user_id=1').fetchone()[0] == 'PASSED'
    assert db.db.execute('SELECT COUNT(*) FROM resource_assignment WHERE user_id=1 AND status=1').fetchone()[0] == 1


@pytest.mark.asyncio
async def test_only_head_office_can_abandon_unassigned_member():
    db = Database()
    db.db.execute('UPDATE resource_assignment SET status=2 WHERE user_id=3')
    for level, org in [('NORMAL', None), ('SUPER', 10)]:
        with pytest.raises(HTTPException) as denied:
            await abandon_workspace_member(db, WorkspaceActor(101, 'A', level, org), 3, '原因')
        assert denied.value.status_code == 403
    await abandon_workspace_member(db, WorkspaceActor(101, 'A', 'SUPER', None), 3, '原因')
    assert db.db.execute('SELECT user_id FROM member_abandonment').fetchone()[0] == 3


@pytest.mark.asyncio
async def test_only_head_office_can_select_matchmaker_for_unassigned_member():
    from app.services.matchmaker_workspace import _require_head_office_member

    db = Database()
    db.db.execute('UPDATE resource_assignment SET status=2 WHERE user_id=3')
    await _require_head_office_member(db, WorkspaceActor(101, 'A', 'SUPER', None), 3)
    for level, org in [('NORMAL', None), ('SUPER', 10)]:
        with pytest.raises(HTTPException) as denied:
            await _require_head_office_member(db, WorkspaceActor(101, 'A', level, org), 3)
        assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as missing:
        await _require_head_office_member(db, WorkspaceActor(101, 'A', 'SUPER', None), 999)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_unassigned_abandoned_member_can_be_claimed():
    db = Database()
    db.db.execute('UPDATE resource_assignment SET status=2 WHERE user_id=3')
    db.db.execute("INSERT INTO member_abandonment(user_id,reason,abandon_type,abandoned_by) VALUES (3,'测试','manual',11)")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    await pickup_workspace_abandoned_member(db, actor, 3)
    assert db.db.execute('SELECT matchmaker_id FROM resource_assignment WHERE user_id=3 AND status=1').fetchone()[0] == 101
    assert db.db.execute('SELECT restored_at FROM member_abandonment WHERE user_id=3').fetchone()[0] is not None


@pytest.mark.asyncio
async def test_abandoned_pool_uses_active_abandonment_not_review_status():
    db = Database()
    db.db.executescript("""
        INSERT INTO matchmaker_member_review(user_id,status) VALUES (2,'REJECTED');
        INSERT INTO member_abandonment(id,user_id,reason,restored_at) VALUES (1,1,'测试弃海',NULL),(2,3,'已捞回','2026-01-01');
    """)
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    page = await list_workspace_members(db, actor, 1, 30, None, None, all_members=True, abandoned=True)
    assert page.total == 1
    assert page.items[0].user_id == 1
    assert page.items[0].abandoned is True
    assert page.items[0].abandoned_reason == '测试弃海'
    assert page.items[0].review_status == 'PENDING'


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
async def test_all_member_pool_matches_crm_and_mine_stays_assigned():
    db = Database()
    db.db.executemany("INSERT INTO users(id,nickname,gender,created_at) VALUES (?, ?, 1, '2026-01-01 00:00:00')", [(i, f'user{i}') for i in range(4, 37)])
    db.db.execute("UPDATE users SET deleted_at='2026-01-01' WHERE id=36")
    db.db.execute("INSERT INTO matchmaker_member_review(user_id,status) VALUES (2,'REJECTED')")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    first = await list_workspace_members(db, actor, 1, 30, None, None, all_members=True)
    second = await list_workspace_members(db, actor, 2, 30, None, None, all_members=True)
    assert first.total == 35 and len(first.items) == 30 and first.has_more
    assert len(second.items) == 5 and not second.has_more
    assert 36 not in [item.user_id for item in first.items + second.items]
    mine = await list_workspace_members(db, actor, 1, 30, None, None, '{"mine":"1"}', all_members=True)
    assert {item.user_id for item in mine.items} == {1, 3}
    rejected = await list_workspace_members(db, actor, 1, 30, 'REJECTED', None, all_members=True)
    assert [item.user_id for item in rejected.items] == [2]


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
    assert page.filter_version == 3


@pytest.mark.asyncio
async def test_workbench_route_forwards_filter_to_scoped_query(monkeypatch):
    from app.api.routes import matchmaker_workspace as route

    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)

    async def authorized_actor(db, current):
        return actor

    monkeypatch.setattr(route, 'require_workspace', authorized_actor)
    page = await route.workbench_members(page=1, page_size=20, review_status=None, search=None, abandoned=False,
                                        filters=json.dumps({'gender': 'female'}), current=object(), db=Database())
    assert [row.user_id for row in page.items] == [1]
    assert page.filter_version == 3


@pytest.mark.asyncio
async def test_online_vip_matches_crm_records_within_scope_and_paginates():
    db = Database()
    db.db.executescript("INSERT INTO user_membership VALUES (1,1,1),(2,1,2),(3,2,1),(4,3,3);")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    page = await list_workspace_members(db, actor, 1, 1, None, None, '{"online_vip":"1"}')
    crm_ids = {row[0] for row in db.db.execute('SELECT DISTINCT user_id FROM user_membership WHERE status <> 3')}
    assert crm_ids == {1, 2}
    assert [row.user_id for row in page.items] == [1]
    assert page.total == 1 and not page.has_more
    db.db.execute('UPDATE user_membership SET status=3 WHERE user_id=1')
    page = await list_workspace_members(db, actor, 1, 1, None, None, '{"online_vip":"1"}')
    assert page.total == 0
    with pytest.raises(HTTPException):
        await list_workspace_members(db, actor, 1, 1, None, None, '{"online_vip":"0"}')


@pytest.mark.asyncio
@pytest.mark.parametrize('vip_filter,seed', [
    ('online_vip', 'INSERT INTO user_membership VALUES (1,1,1),(2,1,2),(3,2,1),(4,3,3)'),
    ('offline_vip', "INSERT INTO offline_vip VALUES (1,1,NULL),(2,1,NULL),(3,2,NULL),(4,3,'2026-01-01')"),
])
async def test_vip_tabs_deduplicate_paginate_and_search_before_limit(vip_filter, seed):
    db = Database()
    db.db.execute(seed)
    db.db.execute("INSERT INTO user_auth(user_id,real_name) VALUES (1,'测试姓名')")
    db.db.execute("UPDATE users SET phone='138-0000-0001' WHERE id=1")
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    filters = json.dumps({vip_filter: '1'})
    first = await list_workspace_members(db, actor, 1, 1, None, None, filters, all_members=True)
    second = await list_workspace_members(db, actor, 2, 1, None, None, filters, all_members=True)
    assert first.total == second.total == 2
    assert [row.user_id for row in first.items] == [2] and first.has_more
    assert [row.user_id for row in second.items] == [1] and not second.has_more
    for keyword in ['A', '测试姓名', '13800000001', '1']:
        found = await list_workspace_members(db, actor, 1, 1, None, keyword, filters, all_members=True)
        assert [row.user_id for row in found.items] == [1] and found.total == 1
    scoped = await list_workspace_members(db, actor, 1, 20, None, None, filters)
    assert [row.user_id for row in scoped.items] == [1]
    db.db.execute("UPDATE users SET deleted_at='2026-01-01' WHERE id=2")
    page = await list_workspace_members(db, actor, 1, 20, None, None, filters, all_members=True)
    assert page.total == 1
    with pytest.raises(HTTPException) as invalid:
        await list_workspace_members(db, actor, 1, 20, None, None, json.dumps({vip_filter: '0'}))
    assert invalid.value.status_code == 422


@pytest.mark.asyncio
async def test_offline_vip_combines_profile_filters_and_keeps_empty_response():
    db = Database()
    db.db.execute('INSERT INTO offline_vip VALUES (1,1,NULL),(2,3,NULL)')
    actor = WorkspaceActor(user_id=101, display_name='A', level='NORMAL', organization_id=10)
    page = await list_workspace_members(db, actor, 1, 1, None, None, '{"offline_vip":"1","gender":"female"}')
    assert page.total == 1 and page.items[0].user_id == 1
    db.db.execute("UPDATE offline_vip SET deleted_at='2026-01-01'")
    empty = await list_workspace_members(db, actor, 1, 20, None, None, '{"offline_vip":"1"}')
    assert empty.items == [] and empty.total == 0 and not empty.has_more
    assert empty.filter_version == 3


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
