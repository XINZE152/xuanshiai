"""属地预检脚本（A12-属地，2026-11-04）单元契约。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "region_preflight.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("region_preflight", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def preflight() -> Any:
    return _load_module()


def _write_segment(path: Path, *, header: str = "prefix,province,city,isp,updated_at") -> None:
    path.write_text(
        header + "\n"
        "1381234,江苏省,南京市,中国移动,2026-10-01\n"
        "1700000,北京市,北京市,虚拟运营商,2026-10-22\n",
        encoding="utf-8",
    )


def test_sha256_missing_returns_none(preflight: Any, tmp_path: Path) -> None:
    assert preflight._sha256(tmp_path / "missing.xdb") is None


def test_phone_segment_status_reads_header_rows_and_date(preflight: Any, tmp_path: Path) -> None:
    path = tmp_path / "phone_segment.csv"
    _write_segment(path)
    status = preflight._phone_segment_status(path)
    assert status["exists"] is True
    assert status["header_ok"] is True
    assert status["rows"] == 2
    assert status["max_updated_at"] == "2026-10-22"


def test_phone_segment_status_rejects_bad_header(preflight: Any, tmp_path: Path) -> None:
    path = tmp_path / "phone_segment.csv"
    _write_segment(path, header="prefix,province")
    status = preflight._phone_segment_status(path)
    assert status["header_ok"] is False


def test_readiness_requires_offline_assets_and_columns(preflight: Any) -> None:
    payload = {
        "provider": "offline",
        "ip": {"exists": True, "size_bytes": 10, "binding_available": True},
        "phone": {"exists": True, "size_bytes": 10, "header_ok": True, "rows": 2},
        "db_checked": True,
        "db": {
            "available": True,
            "columns": {
                "register_ip": True,
                "phone": True,
                "ip_region": True,
                "phone_region": True,
            },
        },
    }
    assert preflight._readiness(payload) == {"ip": True, "phone": True}

    payload["provider"] = "disabled"
    assert preflight._readiness(payload) == {"ip": False, "phone": False}

    payload["provider"] = "offline"
    payload["db"]["columns"]["phone_region"] = False
    assert preflight._readiness(payload) == {"ip": True, "phone": False}


def test_main_returns_2_when_assets_missing(
    preflight: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "region_resolver_provider", "offline")
    monkeypatch.setattr(settings, "region_ip_db_path", str(tmp_path / "missing.xdb"))
    monkeypatch.setattr(settings, "region_phone_segment_path", str(tmp_path / "missing.csv"))

    assert preflight.main(["--skip-db", "--expect-ready"]) == 2


def test_main_ready_with_local_assets(
    preflight: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ip_db = tmp_path / "ip2region_v4.xdb"
    ip_db.write_bytes(b"fake-xdb")
    phone_csv = tmp_path / "phone_segment.csv"
    _write_segment(phone_csv)
    monkeypatch.setattr(settings, "region_resolver_provider", "offline")
    monkeypatch.setattr(settings, "region_ip_db_path", str(ip_db))
    monkeypatch.setattr(settings, "region_phone_segment_path", str(phone_csv))
    monkeypatch.setattr(preflight.ip_offline, "binding_available", lambda: True)

    assert preflight.main(["--skip-db", "--expect-ready", "--json"]) == 0
    output = capsys.readouterr().out
    assert '"ready": {' in output
    assert '"ip": true' in output
    assert '"phone": true' in output
