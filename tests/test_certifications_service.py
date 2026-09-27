import asyncio
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.schemas.certifications import CertificationReviewItem
from app.services.certifications import _item, list_certification_reviews
from app.services.media_access import MEDIA_URL_TTL_SECONDS, verify_media_signature


def test_certification_item_includes_null_material_when_not_submitted() -> None:
    item = _item("education", {}, None)

    assert item["material"] is None
    assert item["material_submitted"] is False


# ---------------- 认证审核列表 SQL 分页回归（与旧 Python 排序/切片逐项对齐） ----------------




class _FakeReviewDb:
    """页查询返回预排序 rows；COUNT 返回预置 total；捕获 SQL 与 params。"""

    def __init__(self, page_rows: list[dict], total: int) -> None:
        self._page_rows = page_rows
        self._total = total
        self.queries: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        captured = dict(params or {})
        self.queries.append((sql, captured))
        if "count(*)" in sql.lower():
            return SimpleNamespace(scalar=lambda: self._total)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: list(self._page_rows)))


def _sorted_rows() -> list[dict]:
    """按新 SQL ORDER BY 语义预排的混合行：
    submitted_at 非空按时间升序，NULL 置后；跨 kind 平局按 kind 字典序决胜。"""
    return [
        {"user_id": 3, "nickname": "早的education", "kind": "education", "status": 1,
         "material": "/storage/uploads/3/education-cert-a.webp",
         "submitted_at": datetime(2026, 9, 1), "reviewed_at": None, "fail_reason": None},
        {"user_id": 1, "nickname": "晚的house", "kind": "house", "status": 1,
         "material": "/storage/uploads/1/house-cert-b.webp",
         "submitted_at": datetime(2026, 9, 10), "reviewed_at": None, "fail_reason": None},
        {"user_id": 1, "nickname": "更晚的education", "kind": "education", "status": 3,
         "material": "/storage/uploads/1/education-cert-c.webp",
         "submitted_at": datetime(2026, 9, 20), "reviewed_at": None, "fail_reason": None},
        {"user_id": 2, "nickname": "平局的house", "kind": "house", "status": 2,
         "material": "/storage/uploads/2/house-cert-d.webp",
         "submitted_at": datetime(2026, 9, 25), "reviewed_at": None, "fail_reason": None},
        {"user_id": 2, "nickname": "平局的marriage", "kind": "marriage", "status": 2,
         "material": "user_confirmed_unmarried",
         "submitted_at": datetime(2026, 9, 25), "reviewed_at": None, "fail_reason": None},
        {"user_id": 5, "nickname": "时间NULL的marriage", "kind": "marriage", "status": 1,
         "material": None,
         "submitted_at": None, "reviewed_at": None, "fail_reason": None},
    ]


def test_review_list_uses_single_union_sql_with_sql_side_paging(monkeypatch) -> None:
    rows = _sorted_rows()
    db = _FakeReviewDb(rows[:3], total=6)

    result = asyncio.run(
        list_certification_reviews(db, page=2, page_size=3, viewer=9)
    )
    assert [item.user_id for item in result.items] == [3, 1, 1]  # 当页 3 行（按 SQL 预排序）

    page_sql, page_params = db.queries[-1]
    assert "UNION ALL" in page_sql, "三 kind 必须合并为单条 UNION ALL 查询"
    assert page_sql.count("'education' AS kind") == 1
    assert page_sql.count("'house' AS kind") == 1
    assert page_sql.count("'marriage' AS kind") == 1
    assert "ORDER BY (submitted_at IS NULL) ASC, submitted_at ASC, user_id ASC, kind ASC" in page_sql
    assert "LIMIT :limit OFFSET :offset" in page_sql
    assert page_params["limit"] == 3 and page_params["offset"] == 3

    count_sql, count_params = db.queries[0]
    assert "count(*)" in count_sql.lower() and "union all" in count_sql.lower()
    assert "limit" not in count_sql.lower()
    assert count_params.get("limit") is None and count_params.get("offset") is None


def test_review_list_global_order_and_schema_are_preserved(monkeypatch) -> None:
    rows = _sorted_rows()
    db = _FakeReviewDb(rows, total=len(rows))

    page = asyncio.run(
        list_certification_reviews(db, page=1, page_size=10, viewer=9)
    )

    assert page.total == len(rows) and page.has_more is False
    # 服务不做二次排序：输出顺序与 SQL 预排序一致（NULL 置后；平局 kind 字典序）
    assert [(item.kind, item.user_id) for item in page.items] == [
        ("education", 3), ("house", 1), ("education", 1),
        ("house", 2), ("marriage", 2), ("marriage", 5),
    ]
    expected_fields = set(CertificationReviewItem.model_fields)
    for item in page.items:
        assert set(item.model_dump()) == expected_fields


def test_review_list_resigns_only_page_rows_for_admin_viewer() -> None:
    rows = _sorted_rows()
    db = _FakeReviewDb(rows, total=len(rows))

    page = asyncio.run(
        list_certification_reviews(db, page=1, page_size=10, viewer=9)
    )

    for item in page.items:
        if not item.material:
            assert item.kind == "marriage", "仅 marriage 时间为 NULL 时材料为空"
            continue
        parsed = urlsplit(item.material)
        if parsed.path.startswith("/storage/uploads/"):
            query = parse_qs(parsed.query)
            assert query["user"] == ["9"], "签名必须绑定 admin viewer"
            assert query["kind"] == ["admin"]
            import time as _time
            now = int(_time.time())
            assert 0 <= int(query["expires"][0]) - now <= MEDIA_URL_TTL_SECONDS
            assert verify_media_signature(
                parsed.path[len("/storage/uploads/"):],
                query,
                category="cert",
                now=now,
            )
        else:
            # marriage 的非 URL 标记串原样返回，不参与签名
            assert item.material in ("user_confirmed_unmarried", "user_not_confirmed_unmarried", None)


def test_review_list_kind_filter_narrows_union_branches() -> None:
    rows = _sorted_rows()
    db = _FakeReviewDb(rows, total=2)

    result = asyncio.run(
        list_certification_reviews(db, page=1, page_size=10, viewer=9, kind="house", status=1, search="张")
    )
    assert result.page_size == 10

    page_sql, page_params = db.queries[-1]
    assert page_sql.count("UNION ALL") == 0, "指定单 kind 时只剩一个分支"
    assert "'house' AS kind" in page_sql
    assert "ua.house_cert IS NOT NULL" in page_sql
    assert "ua.house_verified = :status" in page_sql
    assert page_params["status"] == 1 and page_params["search"] == "张"
    count_sql = db.queries[0][0]
    assert "ua.house_cert IS NOT NULL" in count_sql


def test_review_list_rejects_unknown_kind_with_422() -> None:
    db = _FakeReviewDb([], total=0)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            list_certification_reviews(db, page=1, page_size=10, viewer=9, kind="invalid")
        )
    assert exc_info.value.status_code == 422
    assert db.queries == [], "422 校验在执行任何 SQL 之前完成"
