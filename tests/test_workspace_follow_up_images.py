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
from app.schemas.matchmaker_workspace import WorkspaceMemberFollowUpCreate


class Result:
    def __init__(self, cursor):
        self.cursor = cursor

    def mappings(self):
        return self

    def first(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def one(self):
        return dict(self.cursor.fetchone())

    def all(self):
        return [dict(row) for row in self.cursor.fetchall()]

    @property
    def lastrowid(self):
        return self.cursor.lastrowid


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

    monkeypatch.setattr(service, '_require_owned_or_head_office_member', scope)
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


@pytest.mark.asyncio
async def test_follow_up_scope_for_text_and_images(tmp_path, monkeypatch):
    db = Database()
    db.conn.executescript('''
        DROP TABLE member_follow_up;
        DROP TABLE business_audit_log;
        CREATE TABLE users(id INTEGER PRIMARY KEY, deleted_at TEXT);
        CREATE TABLE resource_assignment(user_id INTEGER, matchmaker_id INTEGER, status INTEGER);
        CREATE TABLE member_follow_up(id INTEGER PRIMARY KEY, user_id INTEGER, method TEXT, content TEXT,
            next_follow_at TEXT, created_by INTEGER, images TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE business_audit_log(actor_user_id INTEGER, action TEXT, resource_type TEXT, resource_id INTEGER, reason TEXT);
        INSERT INTO users VALUES (1,NULL),(2,NULL),(3,NULL),(4,'deleted');
        INSERT INTO resource_assignment VALUES (1,30,1),(2,40,1),(3,30,0),(4,30,1);
    ''')

    async def scalar(query, params):
        row = db.conn.execute(str(query), params).fetchone()
        return row[0] if row else None

    db.scalar = scalar
    monkeypatch.setattr(service.settings, 'upload_dir', str(tmp_path))

    async def run_inline(func, *args):
        return func(*args)

    async def write_inline(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    monkeypatch.setattr(service.asyncio, 'to_thread', run_inline)
    monkeypatch.setattr(member_follow_up_admin, '_fu_write_bytes', write_inline)
    head = WorkspaceActor(99, '总店', 'SUPER', None)
    regular = WorkspaceActor(30, '服务红娘', 'NORMAL', None)
    branch_super = WorkspaceActor(30, '门店超级红娘', 'SUPER', 7)
    request = WorkspaceMemberFollowUpCreate(method='PHONE', content='测试跟进')

    for member in (2, 3):
        record = await service.create_member_follow_up(db, head, member, request)
        assert (await service.list_member_follow_ups(db, head, member, 1, 20)).total == 1
        await service.add_member_follow_up_image(db, head, member, record.id, image_file(), '0')
        assert (await service.get_member_follow_up_image(db, head, member, record.id, 0)).is_file()

    own = await service.create_member_follow_up(db, regular, 1, request)
    assert (await service.list_member_follow_ups(db, branch_super, 1, 1, 20)).total == 1
    await service.add_member_follow_up_image(db, regular, 1, own.id, image_file(), '0')
    assert (await service.get_member_follow_up_image(db, branch_super, 1, own.id, 0)).is_file()

    for actor, member in ((regular, 2), (branch_super, 2), (regular, 3), (head, 4), (regular, 4)):
        operations = (
            lambda: service.list_member_follow_ups(db, actor, member, 1, 20),
            lambda: service.create_member_follow_up(db, actor, member, request),
            lambda: service.get_member_follow_up_image(db, actor, member, 1, 0),
            lambda: service.add_member_follow_up_image(db, actor, member, 1, image_file(), '1'),
        )
        for operation in operations:
            with pytest.raises(HTTPException) as denied:
                await operation()
            assert denied.value.status_code == 404
