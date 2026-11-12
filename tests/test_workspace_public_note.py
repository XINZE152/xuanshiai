from datetime import datetime
import sqlite3

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.matchmaker_workspace import WorkspaceMemberNoteUpdate
from app.services import matchmaker_workspace as service


class Result:
    def __init__(self, row): self.row = row
    def mappings(self): return self
    def first(self): return self.row


class Database:
    def __init__(self): self.note = '后台填写'; self.statements = []; self.committed = False
    async def execute(self, statement, params):
        sql = str(statement)
        self.statements.append(sql)
        if 'INSERT INTO user_profile' in sql: self.note = params['note']
        if 'SELECT matchmaker_note AS note' in sql: return Result({'note': self.note, 'updated_at': datetime(2026, 1, 1)})
        return Result(None)
    async def commit(self): self.committed = True


@pytest.mark.asyncio
async def test_note_reads_and_writes_admin_profile_field(monkeypatch):
    async def scoped(*args): return None
    async def audit(*args): return None
    monkeypatch.setattr(service, '_require_note_member', scoped)
    monkeypatch.setattr(service, '_audit', audit)
    db = Database()
    actor = service.WorkspaceActor(user_id=1, display_name='红娘', level='NORMAL', organization_id=None)
    assert (await service.get_workspace_member_note(db, actor, 39)).note == '后台填写'
    result = await service.update_workspace_member_note(db, actor, 39, WorkspaceMemberNoteUpdate(note='小程序填写'))
    assert result.note == '小程序填写' and db.committed
    assert all('matchmaker_admin_member_note' not in sql for sql in db.statements)


def test_note_matches_admin_length_limit():
    assert len(WorkspaceMemberNoteUpdate(note='字' * 1000).note) == 1000
    with pytest.raises(ValidationError): WorkspaceMemberNoteUpdate(note='字' * 1001)


@pytest.mark.asyncio
async def test_note_access_head_office_and_assigned_members():
    class ScopeDatabase:
        def __init__(self):
            self.db = sqlite3.connect(':memory:')
            self.db.executescript('''
                CREATE TABLE users (id INTEGER PRIMARY KEY, deleted_at TEXT);
                CREATE TABLE resource_assignment (user_id INTEGER, matchmaker_id INTEGER, status INTEGER);
                INSERT INTO users VALUES (1, NULL), (2, NULL), (3, NULL), (4, 'deleted');
                INSERT INTO resource_assignment VALUES (1, 10, 1), (2, 20, 1), (3, 10, 0), (4, 10, 1);
            ''')

        async def scalar(self, statement, params):
            row = self.db.execute(str(statement), params).fetchone()
            return row[0] if row else None

    db = ScopeDatabase()
    head = service.WorkspaceActor(99, '总店', 'SUPER', None)
    regular = service.WorkspaceActor(10, '服务红娘', 'NORMAL', None)
    branch_super = service.WorkspaceActor(10, '门店超级红娘', 'SUPER', 7)
    for member in (1, 2, 3):
        await service._require_note_member(db, head, member)
    for actor in (regular, branch_super):
        await service._require_note_member(db, actor, 1)
        for member in (2, 3, 4):
            with pytest.raises(HTTPException) as denied:
                await service._require_note_member(db, actor, member)
            assert denied.value.status_code == 404
    for member in (4, 999):
        with pytest.raises(HTTPException) as denied:
            await service._require_note_member(db, head, member)
        assert denied.value.status_code == 404
