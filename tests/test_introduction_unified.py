"""The admin and workspace must operate on the same introduction rows."""
import json
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.routes import matchmaker_crm_admin as admin
from app.schemas.matchmaker_crm_admin import MatchRecordCreate, MatchRecordUpdate
from app.schemas.matchmaker_workspace import WorkspaceIntroductionCreate
from app.services.introduction_backfill import backfill_admin_introductions
from app.services.matchmaker_workspace import (
    WorkspaceActor, create_workspace_introduction, list_workspace_introductions,
    update_workspace_introduction_status, view_workspace_introduction_contact,
)


class Result:
    def __init__(self, cursor):
        self.cursor = cursor
        self.lastrowid = cursor.lastrowid

    def mappings(self): return self
    def scalars(self):
        return SimpleNamespace(all=lambda: [row[0] for row in self.cursor.fetchall()])
    def all(self): return [dict(row) for row in self.cursor.fetchall()]
    def first(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None
    def one(self): return dict(self.cursor.fetchone())
    def scalar(self):
        row = self.cursor.fetchone()
        return row[0] if row else None


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function('CONCAT', -1, lambda *v: ''.join(str(x) for x in v))
        self.conn.create_function('LPAD', 3, lambda v, n, c: str(v).rjust(n, c))
        self.conn.create_function('TIMESTAMPDIFF', 3, lambda *_: None)
        self.conn.create_function('JSON_UNQUOTE', 1, lambda value: value)
        self.conn.create_function('JSON_EXTRACT', 2, lambda value, _path: json.loads(value)[0] if value else None)
        self.conn.executescript('''
        CREATE TABLE users(id INTEGER PRIMARY KEY, nickname TEXT, phone TEXT, avatar TEXT, gender INTEGER,
          birthday TEXT, is_single_pledge INTEGER, status INTEGER, deleted_at TEXT);
        CREATE TABLE user_auth(user_id INTEGER, real_name TEXT, realname_status INTEGER);
        CREATE TABLE user_profile(user_id INTEGER, photos TEXT);
        CREATE TABLE user_membership(user_id INTEGER, status INTEGER, start_at TEXT, end_at TEXT);
        CREATE TABLE user_role(user_id INTEGER, role_code TEXT, status INTEGER);
        CREATE TABLE resource_assignment(id INTEGER PRIMARY KEY, user_id INTEGER, matchmaker_id INTEGER, organization_id INTEGER, status INTEGER);
        CREATE TABLE match_apply(id INTEGER PRIMARY KEY, from_user_id INTEGER, to_user_id INTEGER, status INTEGER,
          created_at TEXT, updated_at TEXT, responded_at TEXT);
        CREATE TABLE matchmaker_introduction(id INTEGER PRIMARY KEY, from_user_id INTEGER, to_user_id INTEGER,
          matchmaker_id INTEGER, organization_id INTEGER, status TEXT, failure_reason TEXT, note TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE business_audit_log(id INTEGER PRIMARY KEY, actor_user_id INTEGER, action TEXT, resource_type TEXT, resource_id INTEGER, reason TEXT);
        CREATE TABLE user_match(user_id INTEGER, target_user_id INTEGER, status INTEGER, updated_at TEXT);
        INSERT INTO users VALUES (1,'A','100',NULL,1,NULL,0,1,NULL),(2,'B','200',NULL,2,NULL,0,1,NULL),
          (3,'C','300',NULL,1,NULL,0,1,NULL),(4,'D','400',NULL,2,NULL,0,1,NULL);
        INSERT INTO resource_assignment VALUES (1,1,99,NULL,1),(2,2,99,NULL,1),(3,3,99,NULL,1),(4,4,99,NULL,1);
        INSERT INTO matchmaker_introduction VALUES
          (1,1,2,99,NULL,'PENDING',NULL,'workbench-entry:one','2026-01-01 00:00:00','2026-01-01 00:00:00'),
          (2,3,4,99,NULL,'PENDING',NULL,'workbench-entry:two','2026-01-01 00:00:00','2026-01-01 00:00:00');
        INSERT INTO business_audit_log VALUES (1,99,'create','matchmaker_introduction',1,NULL),
          (2,99,'create','matchmaker_introduction',1,NULL),(3,99,'create','matchmaker_introduction',2,NULL),
          (4,99,'create','matchmaker_introduction',2,NULL);
        ''')
        for i in range(1, 14):
            status = 0 if i <= 4 else 1 if i <= 8 else 2 if i == 9 else 3
            self.conn.execute("INSERT INTO match_apply VALUES (?,?,?,?,?,?,?)", (i, 1, 2, status, '2026-01-01 00:00:00', '2026-01-01 00:00:00', None))

    async def execute(self, query, params=None):
        sql = (str(query).replace(' FOR UPDATE', '').replace('UTC_TIMESTAMP()', 'CURRENT_TIMESTAMP')
               .replace('UTC_DATE()', 'CURRENT_DATE').replace('TIMESTAMPDIFF(YEAR,', "TIMESTAMPDIFF('YEAR',"))
        return Result(self.conn.execute(sql, params or {}))

    async def scalar(self, query, params=None):
        return (await self.execute(query, params)).scalar()

    async def commit(self): self.conn.commit()
    async def rollback(self): self.conn.rollback()


class Admin:
    account = SimpleNamespace(id=99)
    def scope_exists_clause(self, _params, correlation): return '1=1'
    def require(self, _permission): return None


async def admin_page(db):
    return await admin.match_records(page=1, page_size=100, search=None, keyword=None, search_type=None,
                                     date_from=None, date_to=None, matchmaker_id=None, operator_id=None,
                                     status_group=None, current=Admin(), db=db)


async def admin_stats(db):
    return await admin.match_record_statistics(search=None, keyword=None, search_type=None, date_from=None,
        date_to=None, matchmaker_id=None, operator_id=None, current=Admin(), db=db)


@pytest.mark.asyncio
async def test_both_sides_share_migrated_rows_creates_and_statuses(monkeypatch):
    db = Database()
    actor = WorkspaceActor(99, '总店', 'SUPER', None)
    assert (await backfill_admin_introductions(db, apply=True))['missing'] == 13
    await db.commit()
    initial = await admin_page(db)
    workspace = await list_workspace_introductions(db, actor, 1, 100, None, None)
    assert initial.total == workspace.total == 15
    assert {item.id for item in initial.items} == {item.id for item in workspace.items}
    stats = await admin_stats(db)
    assert (stats.total, stats.pending, stats.matching, stats.success, stats.fail, stats.closed) == (15, 6, 0, 4, 1, 4)
    assert await db.scalar('SELECT COUNT(*) FROM business_audit_log WHERE resource_type = :type AND resource_id IN (1,2)', {'type': 'matchmaker_introduction'}) == 4
    assert await db.scalar("SELECT COUNT(*) FROM business_audit_log WHERE action = 'match_record.migrated'", {}) == 13

    linked = await db.scalar("SELECT id FROM matchmaker_introduction WHERE note = 'admin-entry:1'", {})
    await admin.update_match_record(record_id=linked, body=MatchRecordUpdate(status=2, reason='测试'), current=Admin(), db=db)
    assert await db.scalar('SELECT status FROM match_apply WHERE id = 1', {}) == 2
    assert any(item.id == linked and item.status == 'FAILED' for item in
               (await list_workspace_introductions(db, actor, 1, 100, None, None)).items)

    now = datetime(2026, 2, 1)
    created_admin = await admin.create_match_record(MatchRecordCreate(from_love_user_id=1, to_love_user_id=3,
        create_time=now, complete_time=now, line_status=2), current=Admin(), db=db)
    assert any(item.id == created_admin.id and item.status == 'FAILED' for item in
               (await list_workspace_introductions(db, actor, 1, 100, None, None)).items)
    created_workspace = await create_workspace_introduction(db, actor, WorkspaceIntroductionCreate(from_user_id=2, to_user_id=4))
    assert any(item.id == created_workspace.id for item in (await admin_page(db)).items)

    await admin.update_match_record(record_id=created_workspace.id, body=MatchRecordUpdate(status=2, reason='测试'), current=Admin(), db=db)
    assert any(item.id == created_workspace.id and item.status == 'FAILED' for item in
               (await list_workspace_introductions(db, actor, 1, 100, None, None)).items)

    pending_id = 1
    await view_workspace_introduction_contact(db, actor, pending_id, 'from')
    await view_workspace_introduction_contact(db, actor, pending_id, 'to', True)
    assert next(item for item in (await admin_page(db)).items if item.id == pending_id).introduction_status == 'IN_PROGRESS'
    await update_workspace_introduction_status(db, actor, pending_id, 'SUCCEEDED', None, True)
    assert next(item for item in (await admin_page(db)).items if item.id == pending_id).status == 1
    stats = await admin_stats(db)
    assert (stats.total, stats.pending, stats.matching, stats.success, stats.fail, stats.closed) == (17, 4, 0, 5, 4, 4)

    async def begin(*_args, **_kwargs): return None, None
    async def finish(*_args, **_kwargs): return None
    monkeypatch.setattr(admin, 'begin_sensitive_operation', begin)
    monkeypatch.setattr(admin, 'finish_sensitive_operation', finish)
    await admin.match_record_delete(record_id=linked, reason='测试删除', idempotency_key='test', current=Admin(), db=db)
    assert linked not in {item.id for item in (await admin_page(db)).items}
    assert linked not in {item.id for item in (await list_workspace_introductions(db, actor, 1, 100, None, None)).items}
    result = await backfill_admin_introductions(db, apply=True)
    assert result['deleted'] == 1 and result['missing'] == 0
