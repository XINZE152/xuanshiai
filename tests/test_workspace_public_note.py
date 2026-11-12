from datetime import datetime

import pytest
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
    monkeypatch.setattr(service, '_require_scoped_member', scoped)
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
