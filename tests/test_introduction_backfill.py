from datetime import datetime

import pytest

from app.services import introduction_backfill as migration


class Result:
    def __init__(self, rows, lastrowid=None):
        self.rows = rows
        self.lastrowid = lastrowid

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Database:
    def __init__(self):
        self.applications = [
            dict(id=i, from_user_id=i, to_user_id=i + 100, status=status,
                 created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1), responded_at=None)
            for i, status in enumerate((0, 1, 2, 3), start=1)
        ]
        self.introductions = []
        self.audits = []

    async def scalar(self, statement, params):
        if 'match_record.unified_deleted' in str(statement):
            return 1 if any(item.get('deleted_id') == params['id'] for item in self.audits) else None
        if 'SELECT reason FROM business_audit_log' in str(statement):
            return '历史原因' if params['id'] == 3 else None
        raise AssertionError(str(statement))

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM match_apply" in sql:
            return Result(self.applications)
        if "FROM matchmaker_introduction WHERE note" in sql:
            return Result([item for item in self.introductions if item['note'] in params.values()])
        if "INSERT INTO matchmaker_introduction" in sql:
            self.introductions.append(dict(params, id=len(self.introductions) + 1))
            return Result([], lastrowid=len(self.introductions))
        if "INSERT INTO business_audit_log" in sql:
            self.audits.append(params)
            return Result([])
        raise AssertionError(sql)


@pytest.mark.asyncio
async def test_backfill_preserves_all_statuses_and_is_idempotent(monkeypatch):
    async def owner(*_args):
        return None

    monkeypatch.setattr(migration, '_match_record_follow_up_owner', owner)
    db = Database()
    assert await migration.backfill_admin_introductions(db, apply=False) == {'applications': 4, 'linked': 0, 'missing': 4, 'deleted': 0}
    assert db.introductions == []
    assert await migration.backfill_admin_introductions(db, apply=True) == {'applications': 4, 'linked': 0, 'missing': 4, 'deleted': 0}
    assert [item['status'] for item in db.introductions] == ['PENDING', 'SUCCEEDED', 'FAILED', 'CLOSED']
    assert db.introductions[2]['reason'] == '历史原因'
    assert len(db.audits) == 4
    assert await migration.backfill_admin_introductions(db, apply=True) == {'applications': 4, 'linked': 4, 'missing': 0, 'deleted': 0}
    assert len(db.introductions) == 4
    assert len(db.audits) == 4


@pytest.mark.asyncio
async def test_deleted_introduction_is_not_recreated(monkeypatch):
    async def owner(*_args):
        return None

    monkeypatch.setattr(migration, '_match_record_follow_up_owner', owner)
    db = Database()
    db.audits.append({'deleted_id': 1})
    result = await migration.backfill_admin_introductions(db, apply=True)
    assert result == {'applications': 4, 'linked': 0, 'missing': 3, 'deleted': 1}
    assert all(row['note'] != 'admin-entry:1' for row in db.introductions)


@pytest.mark.asyncio
async def test_backfill_refuses_duplicate_links_and_status_conflict(monkeypatch):
    async def owner(*_args):
        return None

    monkeypatch.setattr(migration, '_match_record_follow_up_owner', owner)
    db = Database()
    db.introductions = [dict(id=10, from_user_id=1, to_user_id=101, status='PENDING', note='admin-entry:1'),
                        dict(id=11, from_user_id=1, to_user_id=101, status='PENDING', note='discovery-apply:1')]
    with pytest.raises(ValueError, match='Multiple introduction links'):
        await migration.backfill_admin_introductions(db, apply=True)
    db.introductions = [dict(id=10, from_user_id=2, to_user_id=102, status='FAILED', note='admin-entry:2')]
    with pytest.raises(ValueError, match='status conflicts'):
        await migration.backfill_admin_introductions(db, apply=True)
