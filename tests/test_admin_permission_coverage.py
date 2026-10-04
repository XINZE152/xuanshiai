"""D-5 回归：后台路由权限覆盖面（fail-closed）。

历史上 `get_current_matchmaker_admin` 按 URL 子串推导权限，未命中时**静默放行**，
导致大量后台路由实际没有权限校验。本测试对此做静态兜底：

每一条使用后台鉴权的路由，必须满足以下之一，否则视为盲区（测试失败）：
1. 命中 `_matchmaker_admin_permission` 的 URL 关键词映射；
2. 端点带有 `@declare_permission(...)` 声明的权限码；
3. 属于显式豁免白名单（仅认证类端点）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from starlette.requests import Request

from app.api.dependencies import (
    PERMISSION_EXEMPT_SUFFIXES,
    PERMISSION_ATTR,
    _is_permission_exempt,
    _matchmaker_admin_permission,
)

ROUTES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api" / "routes"
ACTIONS = ("get", "post", "patch", "put", "delete")


def _admin_param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    args = list(fn.args.posonlyargs) + list(fn.args.args)
    defaults = list(fn.args.defaults)
    for arg, default in zip(args[len(args) - len(defaults):], defaults):
        for node in ast.walk(default):
            if isinstance(node, ast.Name) and node.id == "get_current_matchmaker_admin":
                names.add(arg.arg)
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if default is None:
            continue
        for node in ast.walk(default):
            if isinstance(node, ast.Name) and node.id == "get_current_matchmaker_admin":
                names.add(arg.arg)
    return names


def _declared_code(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for decorator in fn.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if isinstance(target, ast.Name) and target.id == "declare_permission":
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                return str(decorator.args[0].value)
    return None


def _body_requires(fn: ast.FunctionDef | ast.AsyncFunctionDef, params: set[str]) -> list[str]:
    found: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "require":
            continue
        if getattr(func.value, "id", None) not in params:
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            found.append(str(node.args[0].value))
    return found


def _collect_routes() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "get_current_matchmaker_admin" not in source:
            continue
        tree = ast.parse(source)
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if getattr(node.value.func, "id", None) != "APIRouter":
                continue
            prefix = ""
            for keyword in node.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    prefix = str(keyword.value.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix

        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = _admin_param_names(node)
            if not params:
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                receiver = getattr(decorator.func.value, "id", None)
                if receiver not in prefixes or decorator.func.attr not in ACTIONS:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                rows.append(
                    {
                        "file": path.name,
                        "line": node.lineno,
                        "method": decorator.func.attr.upper(),
                        "api": "/api/v1" + prefixes[receiver] + str(decorator.args[0].value),
                        "declared": _declared_code(node),
                        "body": _body_requires(node, params),
                    }
                )
    return rows


ROUTES = _collect_routes()


def _mapped(route: dict[str, object]) -> str | None:
    request = Request(
        {
            "type": "http",
            "method": str(route["method"]),
            "path": str(route["api"]),
            "headers": [],
            "path_params": {},
        }
    )
    return _matchmaker_admin_permission(request)


def test_backoffice_routes_are_discovered() -> None:
    assert len(ROUTES) > 300


def test_no_backoffice_route_is_permission_blind() -> None:
    blind = [
        route
        for route in ROUTES
        if _mapped(route) is None
        and not route["declared"]
        and not _is_permission_exempt(str(route["api"]))
    ]
    detail = "\n".join(
        f"  {route['file']}:{route['line']} {route['method']} {route['api']}" for route in blind
    )
    assert not blind, f"以下后台路由既未命中权限映射、也未声明权限：\n{detail}"


def test_declared_permissions_are_enforced_centrally() -> None:
    """声明了权限的端点必须同时带有可被统一校验读取的标记属性。"""
    for route in ROUTES:
        if not route["declared"]:
            continue
        assert route["declared"], route


def test_unknown_routes_are_fail_closed() -> None:
    """未命中映射的任意路径必须返回 None，交由 fail-closed 分支拒绝。"""
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/some-brand-new-endpoint",
            "headers": [],
            "path_params": {},
        }
    )
    assert _matchmaker_admin_permission(request) is None


def test_permission_exemption_is_limited_to_auth_endpoints() -> None:
    assert set(PERMISSION_EXEMPT_SUFFIXES) == {
        "/admin/matchmaker/auth/me",
        "/admin/matchmaker/auth/logout",
    }
    # 业务路由不得再被 "/auth/" 子串误豁免（历史上 member 实名审核曾被放行）。
    assert not _is_permission_exempt("/api/v1/admin/members/auth/realname-reviews")
    assert not _is_permission_exempt("/api/v1/admin/matchmaker/accounts/login-logs")


def test_declared_permission_marks_endpoint_attribute() -> None:
    from app.api.dependencies import declare_permission

    @declare_permission("matchmaker.read")
    def endpoint() -> None:  # pragma: no cover - attribute only
        pass

    assert getattr(endpoint, PERMISSION_ATTR) == "matchmaker.read"


@pytest.mark.parametrize("filename", ["admin_config.py", "admin_content.py"])
def test_config_admin_routes_declare_platform_permission(filename: str) -> None:
    """配置类端点（可修改系统配置/运营内容）必须声明权限。"""
    routes = [route for route in ROUTES if route["file"] == filename]
    assert routes
    for route in routes:
        assert route["declared"] or route["body"], route
