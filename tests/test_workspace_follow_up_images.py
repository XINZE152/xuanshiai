"""WORKSPACE-FOLLOWUP-IMAGES-20261111: shared private follow-up images."""
import json
import sqlite3
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import matchmaker_workspace as service
from app.services import member_follow_up_admin
from app.services.matchmaker_workspace import WorkspaceActor


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def mappings(self):
        return self

    def first(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE member_follow_up(id INTEGER PRIMARY KEY,user_id INTEGER,created_by INTEGER,images TEXT);
        CREATE TABLE business_audit_log(actor_user_id INTEGER,action TEXT,resource_type TEXT,resource_id INTEGER,reason TEXT);
        INSERT INTO member_follow_up VALUES (10,20,30,NULL),(11,20,30,NULL);
        INSERT INTO business_audit_log VALUES (30,'matchmaker_workspace.member.follow_up.create','member_follow_up',10,NULL);
        """)

    async def execute(self, sql, params):
        return Result(self.conn.execute(str(sql).replace(' FOR UPDATE', ''), params))

    async def commit(self):
        self.conn.commit()

    async def rollback(self):
        self.conn.rollback()


def image_file():
    buffer = SpooledTemporaryFile()
    Image.new('RGB', (2, 2), 'red').save(buffer, format='PNG')
    buffer.seek(0)
    return UploadFile(file=buffer, filename='image.png')


def test_routes_require_user_authentication():
    client = TestClient(app)
    prefix = '/api/v1/matchmaker/workbench/members/20/follow-ups/10/images'
    assert client.get(prefix + '/0').status_code == 401
    assert client.post(prefix, data={'upload_key': '0'}, files={'file': ('x.png', b'x', 'image/png')}).status_code == 401


@pytest.mark.asyncio
async def test_images_share_admin_storage_scope_and_limit(tmp_path, monkeypatch):
    db = Database()
    actor = WorkspaceActor(30, '红娘', 'NORMAL', None)

    async def scope(_db, current, member):
        if current.user_id != 30 or member != 20:
            raise HTTPException(404)

    monkeypatch.setattr(service, '_require_scoped_member', scope)
    monkeypatch.setattr(service.settings, 'upload_dir', str(tmp_path))
    async def run_inline(func, *args):
        return func(*args)

    async def write_inline(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    monkeypatch.setattr(service.asyncio, 'to_thread', run_inline)
    monkeypatch.setattr(member_follow_up_admin, '_fu_write_bytes', write_inline)
    for index in range(9):
        await service.add_member_follow_up_image(db, actor, 20, 10, image_file(), str(index))
    await service.add_member_follow_up_image(db, actor, 20, 10, image_file(), '0')
    paths = json.loads(db.conn.execute('SELECT images FROM member_follow_up WHERE id=10').fetchone()[0])
    assert len(paths) == 9
    assert paths[0].startswith('/storage/uploads/20/follow-up-img-')
    assert (await service.get_member_follow_up_image(db, actor, 20, 10, 0)).read_bytes().startswith(b'RIFF')
    # Admin and workspace records use the same stored paths and reader.
    db.conn.execute('UPDATE member_follow_up SET images = ? WHERE id=11', (json.dumps(paths[:1]),))
    assert (await service.get_member_follow_up_image(db, actor, 20, 11, 0)).read_bytes().startswith(b'RIFF')
    with pytest.raises(HTTPException) as full:
        await service.add_member_follow_up_image(db, actor, 20, 10, image_file(), '9')
    assert full.value.status_code == 422
    with pytest.raises(HTTPException):
        await service.get_member_follow_up_image(db, WorkspaceActor(31, '其他', 'NORMAL', None), 20, 10, 0)
    with pytest.raises(HTTPException):
        await service.add_member_follow_up_image(db, actor, 20, 11, image_file(), '0')
    db.conn.execute("UPDATE member_follow_up SET images = '[\"/storage/uploads/20/../../secret\"]' WHERE id=10")
    with pytest.raises(HTTPException):
        await service.get_member_follow_up_image(db, actor, 20, 10, 0)
