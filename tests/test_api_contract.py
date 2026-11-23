"""前后端接口对账脚本（`scripts/check_api_contract.py`）的契约测试。

背景：2026-11-23 安全中心因为「前端调了 /security/center、后端没这个路由」全站 404，
前后端各自的测试都是绿的。这里锁定四件事：

1. 字面量抽取只认站内接口路径（跳过绝对地址、页面路由、重复项）；
2. 匹配支持精确 / 动态拼接前缀 / ``{param}`` 模板三种形态；
3. 基线条目必须带原因，且**已实现的接口不能留在基线里**（否则闸门会被悄悄放宽）；
4. 新增失配必须让脚本失败（退出码 1）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_api_contract as contract

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "api_contract_baseline.json"


def test_normalize_path_strips_query_and_trailing_slash() -> None:
    assert contract.normalize_path("/users/me/profile?x=1") == "/users/me/profile"
    assert contract.normalize_path("/community/posts/") == "/community/posts"
    assert contract.normalize_path("/") == "/"
    assert contract.normalize_path("/security/center") == "/security/center"


def test_openapi_path_set_drops_api_prefix() -> None:
    paths = contract.openapi_path_set(["/api/v1/security/center", "/health"])
    assert paths == {"/security/center", "/health"}


def test_extract_urls_skips_noise_and_dedupes() -> None:
    text = """
      request({ url: '/users/me/profile', method: 'GET' })
      request({ url: '/users/me/profile', method: 'PATCH' })
      request({ url: 'https://example.com/api', method: 'GET' })
      uni.navigateTo({ url: '/pages/auth/login' })
      request({ url: '/community/posts/' + postId, method: 'GET' })
    """
    assert contract.extract_urls(text) == ["/users/me/profile", "/community/posts"]


def test_matches_supports_exact_prefix_and_template() -> None:
    path_set = {"/users/me/profile", "/community/posts", "/users/{target_id}/like"}
    templates = contract.compile_templates(path_set)
    assert contract.matches("/users/me/profile", path_set, templates)
    # 动态拼接：'/community/posts' + '/' + id 会被抽成前缀
    assert contract.matches("/community/posts", path_set, templates)
    # 后端是模板、前端写死 id
    assert contract.matches("/users/42/like", path_set, templates)
    assert not contract.matches("/security/center", path_set, templates)


def test_baseline_entries_are_documented_and_not_stale() -> None:
    payload = json.loads(BASELINE.read_text(encoding="utf-8"))
    entries = payload["entries"]
    assert entries, "基线不应为空；若所有失配都已解决，请删除基线文件"
    for entry in entries:
        assert entry["file"].endswith(".uts"), entry
        assert entry["url"].startswith("/"), entry
        assert entry["reason"].strip() != "", f"基线条目缺原因：{entry}"
        assert entry["reason"] != "待评估", f"基线条目原因未填写：{entry}"
    urls = {entry["url"] for entry in entries}
    # 已实现的接口若还留在基线里，等于永久豁免了这条闸门
    assert "/security/center" not in urls, "安全中心接口已实现，不应再留在基线"


def test_new_gap_fails_the_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frontend = tmp_path / "vue"
    (frontend / "api").mkdir(parents=True)
    (frontend / "api" / "demo.uts").write_text(
        "request({ url: '/security/center', method: 'GET' })\n"
        "request({ url: '/definitely/not/implemented', method: 'GET' })\n",
        encoding="utf-8",
    )
    # 用最小 OpenAPI 替身，避免在单测里拉起整个 FastAPI 应用
    monkeypatch.setattr(contract, "scan", lambda _dir: ([("/security/center", "/security/center")], [("demo.uts", "/definitely/not/implemented")]))
    monkeypatch.setattr(contract, "BASELINE_PATH", tmp_path / "baseline.json")
    (tmp_path / "baseline.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8"
    )
    assert contract.main(["--frontend", str(frontend)]) == 1


def test_baselined_gap_does_not_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    frontend = tmp_path / "vue"
    (frontend / "api").mkdir(parents=True)
    monkeypatch.setattr(contract, "scan", lambda _dir: ([], [("demo.uts", "/legacy/path")]))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"entries": [{"file": "demo.uts", "url": "/legacy/path", "reason": "历史路径"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "BASELINE_PATH", baseline)
    assert contract.main(["--frontend", str(frontend)]) == 0
