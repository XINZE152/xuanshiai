from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.matchmaker_workspace import WorkspaceActor, list_workspace_leads


@pytest.mark.asyncio
async def test_workspace_filters_keep_scope_and_order():
    actor = WorkspaceActor(user_id=7, display_name="test", level="NORMAL", organization_id=3)
    result = SimpleNamespace(items=[SimpleNamespace(id=2), SimpleNamespace(id=1)], total=5, has_more=True)
    rows = [{"id": 1}, {"id": 2}]
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))))
    def lead(row):
        return SimpleNamespace(id=row["id"])
    with patch("app.services.customer_lead_admin.list_leads", new=AsyncMock(return_value=result)) as query, patch("app.services.matchmaker_workspace._lead_from_row", side_effect=lead), patch("app.services.matchmaker_workspace.WorkspaceLeadPage", side_effect=lambda **kw: SimpleNamespace(**kw)):
        page = await list_workspace_leads(db, actor, 1, 2, None, None, gender=2, order="earliest")
    assert [item.id for item in page.items] == [2, 1]
    assert page.total == 5 and page.has_more
    assert query.await_args.kwargs["gender"] == 2
    assert query.await_args.kwargs["order"] == "earliest"
    assert "abandoned_by" in query.await_args.kwargs["scope"]
    assert query.await_args.kwargs["scope_params"]["scope_matchmaker_id"] == 7
    sql, params = db.execute.await_args.args
    assert "l.matchmaker_id = :scope_matchmaker_id" in str(sql)
    assert params["abandoned_by"] == 7


@pytest.mark.asyncio
async def test_workspace_empty_page_does_not_fetch_details():
    actor = WorkspaceActor(user_id=7, display_name="test", level="SUPER", organization_id=9)
    result = SimpleNamespace(items=[], total=0, has_more=False)
    db = SimpleNamespace(execute=AsyncMock())
    with patch("app.services.customer_lead_admin.list_leads", new=AsyncMock(return_value=result)) as query:
        page = await list_workspace_leads(db, actor, 1, 20, None, None, stored=False)
    assert page.items == []
    assert query.await_args.kwargs["scope_params"]["scope_organization_id"] == 9
    assert query.await_args.kwargs["stored"] is False
    db.execute.assert_not_awaited()


def test_route_validates_and_forwards_filters():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.dependencies import get_current_user
    from app.api.routes import matchmaker_workspace as route
    from app.db.session import get_db

    app = FastAPI()
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: object()
    app.dependency_overrides[get_db] = lambda: object()
    response_data = {"items": [], "page": 1, "page_size": 20, "total": 0, "has_more": False}
    with patch.object(route, "require_workspace", new=AsyncMock(return_value=object())), patch.object(route, "list_workspace_leads", new=AsyncMock(return_value=response_data)) as query:
        with TestClient(app) as client:
            response = client.get("/matchmaker/workbench/leads", params={"gender": 2, "stored": "false", "order": "earliest"})
            invalid = client.get("/matchmaker/workbench/leads", params={"gender": 9})
    assert response.status_code == 200
    assert response.json() == response_data
    assert invalid.status_code == 422
    assert query.await_count == 1
    assert query.await_args.kwargs["gender"] == 2
    assert query.await_args.kwargs["stored"] is False
    assert query.await_args.kwargs["order"] == "earliest"
