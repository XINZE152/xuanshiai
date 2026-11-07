"""红娘工作台「红娘喜讯」读写路径契约。

口径（2026-10-27 定）：喜讯数据不另建表，读后台「运营工具-喜讯管理」写入的
admin_content_item(domain=good_news / good_news_pennant)——后台录一条，红娘这边就能看到。
范围与其它中心一致：总店（SUPER）看全部，普通红娘只看 extra.matchmaker_id 是自己的行。
红娘端「报喜-反馈喜讯」也写这张表：落 status=2 待审行，所以 SELF 范围放宽到 status IN (1, 2)，
她在自己工作台立刻看得到，别人要等后台放行。
全部用 StubDB 按 SQL 文本分派，不依赖 MySQL。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.schemas.matchmaker_workspace import WorkspaceGoodNewsCreate
from app.services.matchmaker_workspace import (
    WorkspaceActor,
    create_workbench_good_news,
    list_workbench_good_news,
    list_workbench_good_news_pennants,
)

CREATED_AT = datetime(2026, 10, 20, 1, 0, 0)


class StubResult:
    def __init__(self, rows: list[dict[str, object]], lastrowid: int = 0) -> None:
        self._rows = rows
        self.lastrowid = lastrowid

    def mappings(self) -> "StubResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class StubDB:
    def __init__(self, *, rows: list[dict[str, object]], total: int, profiles: dict[int, str]) -> None:
        self.rows = rows
        self.total = total
        self.profiles = profiles
        self.list_sql = ""
        self.list_params: dict[str, object] = {}
        self.insert_sql = ""
        self.insert_params: dict[str, object] = {}
        self.committed = False

    async def scalar(self, sql: object, params: dict | None = None) -> object:
        text = str(sql)
        if "SELECT COUNT(*) FROM admin_content_item" in text:
            self.count_params = dict(params or {})
            return self.total
        raise AssertionError(f"未预期的读查询：{text}")

    async def commit(self) -> None:
        self.committed = True

    async def execute(self, sql: object, params: dict | None = None) -> StubResult:
        text = str(sql)
        if "INSERT INTO admin_content_item" in text:
            self.insert_sql = text
            self.insert_params = dict(params or {})
            return StubResult([], lastrowid=77)
        if "FROM admin_content_item" in text:
            self.list_sql = text
            self.list_params = dict(params or {})
            return StubResult(self.rows)
        if "FROM users WHERE id IN" in text:
            wanted = [int(value) for value in (params or {}).values()]
            return StubResult([
                {"id": user_id, "nickname": self.profiles[user_id], "avatar": f"/static/{user_id}.jpg"}
                for user_id in wanted
            ])
        raise AssertionError(f"未预期的语句：{text}")


def _row(
    content_id: int, extra: dict[str, object], *, image_url: str | None = None, status: int = 1
) -> dict[str, object]:
    return {
        "id": content_id,
        "title": "标题",
        "subtitle": "后台副标题里的红娘名",
        "image_url": image_url,
        "status": status,
        "extra_json": json.dumps(extra, ensure_ascii=False),
        "created_at": CREATED_AT,
    }


def _super() -> WorkspaceActor:
    return WorkspaceActor(user_id=2, display_name="红娘测试账号", level="SUPER", organization_id=None)


def _normal() -> WorkspaceActor:
    return WorkspaceActor(user_id=21, display_name="钱多多（示例）", level="NORMAL", organization_id=3)


@pytest.mark.asyncio
async def test_super_matchmaker_reads_the_admin_good_news_domain() -> None:
    db = StubDB(
        rows=[_row(1, {
            "status": "牵手成功", "matchmaker_id": 2, "matchmaker_name": "红娘测试账号",
            "from_user_id": 18, "to_user_id": 12, "feedback_name": "被覆盖的旧姓名", "partner_name": "沈知意（演示）",
            "meet_duration": "1", "content_html": "<p>首次约见后&nbsp;两人自己加了微信。</p>",
            "blessing": "愿你们把日子过成糖。", "public": True, "blessing_count": 6,
            "images": ["/static/a.jpg", "", "/static/b.jpg"],
        })],
        total=1,
        profiles={18: "姜疏影（演示）", 12: "沈知意（演示）"},
    )
    page = await list_workbench_good_news(db, _super(), "", 1, 100)

    assert db.list_params["domain"] == "good_news"
    # 总店不按归属收窄整表：matchmaker_id 只出现在「status = 1 OR ...」里放行自己的待审行。
    assert "AND JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.matchmaker_id')) = " not in db.list_sql
    assert db.list_params["limit"] == 100
    item = page.items[0]
    assert item.stage == "牵手成功"
    # 有会员 ID 时昵称以 users 为准，后台手填的旧姓名只做回退。
    assert (item.from_name, item.to_name) == ("姜疏影（演示）", "沈知意（演示）")
    assert item.from_avatar == "/static/18.jpg"
    assert item.meet_duration == "1个月"
    assert item.images == ["/static/a.jpg", "/static/b.jpg"]
    assert item.content == "首次约见后 两人自己加了微信。"
    assert item.blessing_count == 6
    assert item.public is True
    assert page.total == 1 and page.has_more is False


@pytest.mark.asyncio
async def test_rows_without_member_ids_fall_back_to_names_cover_and_text_duration() -> None:
    db = StubDB(
        rows=[_row(4, {
            "status": "已订婚", "matchmaker_name": "琴琴", "feedback_name": "王先生", "partner_name": "李女士",
            "meet_duration": "两年零三个月", "content_html": "",
        }, image_url="/static/cover.jpg")],
        total=1,
        profiles={},
    )
    page = await list_workbench_good_news(db, _super(), "", 1, 20)
    item = page.items[0]

    assert (item.from_user_id, item.to_user_id) == (None, None)
    assert (item.from_name, item.to_name) == ("王先生", "李女士")
    assert item.meet_duration == "两年零三个月"
    # extra.images 缺失时回退到行级封面图；祝福计数缺失按 0。
    assert item.images == ["/static/cover.jpg"]
    assert item.blessing_count == 0
    assert item.blessing == ""
    assert item.public is True


@pytest.mark.asyncio
async def test_ordinary_matchmaker_only_seeds_rows_assigned_to_her() -> None:
    db = StubDB(rows=[], total=0, profiles={})
    await list_workbench_good_news(db, _normal(), "恋爱生活", 1, 20)

    assert "JSON_EXTRACT(extra_json, '$.matchmaker_id')" in db.list_sql
    assert db.list_params["own_matchmaker_id"] == "21"
    assert "JSON_EXTRACT(extra_json, '$.status')" in db.list_sql
    assert db.list_params["stage"] == "恋爱生活"


@pytest.mark.asyncio
async def test_pennant_wall_matches_the_receiving_matchmaker_by_display_name() -> None:
    row = _row(5, {
        "maker": "用户 姜疏影（演示）", "giver": "姜疏影 & 沈知意",
        "target": "红娘测试账号", "words": "牵线搭桥成就姻缘",
    })
    db = StubDB(rows=[row], total=1, profiles={})
    page = await list_workbench_good_news_pennants(db, _super(), 1, 20)
    assert db.list_params["domain"] == "good_news_pennant"
    assert "status = 1 OR" in db.list_sql
    assert "$.target_user_id" in db.list_sql  # Own pending gifts remain visible; other pending gifts do not.
    assert page.items[0].target == "红娘测试账号"
    assert page.items[0].words == "牵线搭桥成就姻缘"

    scoped = StubDB(rows=[], total=0, profiles={})
    await list_workbench_good_news_pennants(scoped, _normal(), 1, 20)
    assert scoped.list_params["own_target"] == "钱多多（示例）"


@pytest.mark.asyncio
async def test_matchmaker_report_writes_a_pending_good_news_row() -> None:
    db = StubDB(rows=[], total=0, profiles={})
    created = await create_workbench_good_news(
        db, _normal(),
        WorkspaceGoodNewsCreate(
            stage="牵手成功", partner_name=" 沈知意 ", meet_duration=" 6 ",
            content=" 首次约见后两人自己加了微信。 ", images=["/storage/uploads/good-news/photo-a.webp", ""],
        ),
    )

    assert (created.id, created.pending_review) == (77, True)
    assert db.committed is True
    assert db.insert_params["domain"] == "good_news"
    # 红娘代报先进待审：status=2，由后台喜讯管理放行后才对其它范围可见。
    assert "VALUES (1, :domain, :title, :subtitle, :image_url, 2, 0, :extra, :created_by)" in db.insert_sql
    assert db.insert_params["title"] == "牵手成功"
    assert db.insert_params["image_url"] == "/storage/uploads/good-news/photo-a.webp"

    extra = json.loads(db.insert_params["extra"])
    assert extra["status"] == "牵手成功"
    # 红娘端没有会员选择器，双方只存姓名文本，读侧按文本回退。
    assert extra["partner_name"] == "沈知意" and "from_user_id" not in extra
    assert extra["matchmaker_id"] == "21" and extra["matchmaker_name"] == "钱多多（示例）"
    assert extra["meet_duration"] == "6" and extra["content_html"] == "首次约见后两人自己加了微信。"
    assert extra["images"] == ["/storage/uploads/good-news/photo-a.webp"]


@pytest.mark.asyncio
async def test_matchmaker_report_rejects_blank_stage_or_partner_name() -> None:
    db = StubDB(rows=[], total=0, profiles={})
    with pytest.raises(HTTPException) as error:
        await create_workbench_good_news(
            db, _normal(), WorkspaceGoodNewsCreate(stage="牵手成功", partner_name="   ")
        )

    assert error.value.status_code == 422
    assert db.insert_sql == ""


@pytest.mark.asyncio
async def test_pending_reports_stay_visible_to_the_reporting_matchmaker_only() -> None:
    scoped = StubDB(rows=[], total=0, profiles={})
    await list_workbench_good_news(scoped, _normal(), "", 1, 20)
    assert "status IN (1, 2)" in scoped.list_sql
    assert "JSON_EXTRACT(extra_json, '$.matchmaker_id')" in scoped.list_sql

    platform = StubDB(
        rows=[_row(9, {"status": "已领证", "partner_name": "李女士", "feedback_name": "王先生"}, status=2)],
        total=1,
        profiles={},
    )
    page = await list_workbench_good_news(platform, _super(), "", 1, 20)
    assert "status IN (1, 2)" not in platform.list_sql
    # 总店只看已放行行，但自己代报的待审行要留在她的工作区里，否则报完喜看不到。
    # 归属只认 extra.matchmaker_id：created_by 在后台写入时存的是管理员 ID，与 C 端 user_id 同号段会漏出别人的隐藏行。
    assert "(status = 1 OR JSON_UNQUOTE(JSON_EXTRACT(extra_json, '$.matchmaker_id')) = :own_matchmaker_id)" in platform.list_sql
    assert platform.list_params["own_matchmaker_id"] == "2"
    assert page.items[0].pending_review is True
