"""A12 属地刷新上线预检（只读）。

检查 IP 库、查询绑定、手机号段表、users 列与候选数量，不写任何数据。
用于服务器发布前后确认「资源是否真的就绪」，避免接口只在运行时返回 503。

用法::

    python scripts/region_preflight.py
    python scripts/region_preflight.py --expect-ready
    python scripts/region_preflight.py --json --skip-db
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.region import ip_offline, phone_offline, registry  # noqa: E402


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ip_status() -> dict[str, Any]:
    path = ip_offline.resolve_db_path()
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path),
        "binding_available": ip_offline.binding_available(),
    }


def _phone_segment_status(path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "header_ok": False,
        "rows": 0,
        "max_updated_at": None,
    }
    if not path.is_file() or status["size_bytes"] <= 0:
        return status
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            status["header_ok"] = {"prefix", "province", "city"}.issubset(fields)
            dates: list[str] = []
            for row in reader:
                if (row.get("prefix") or "").strip().isdigit():
                    status["rows"] += 1
                updated_at = (row.get("updated_at") or "").strip()
                if updated_at:
                    dates.append(updated_at)
            status["max_updated_at"] = max(dates) if dates else None
    except (OSError, csv.Error):
        status["header_ok"] = False
    return status


async def _db_status() -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.session import session_factory

    if session_factory is None:
        return {"available": False, "error": "database driver not installed"}

    async with session_factory() as db:
        columns = {
            str(row[0])
            for row in (
                await db.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS"
                        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'"
                    )
                )
            ).all()
        }
        result: dict[str, Any] = {
            "available": True,
            "columns": {
                name: name in columns
                for name in ("register_ip", "phone", "ip_region", "phone_region")
            },
            "total": None,
            "empty_ip": None,
            "empty_phone": None,
            "filled_ip": None,
            "filled_phone": None,
        }
        parts = ["COUNT(*) AS total"]
        if "register_ip" in columns:
            parts.append("SUM(register_ip IS NULL OR register_ip = '') AS empty_ip")
        if "phone" in columns:
            parts.append("SUM(phone IS NULL OR phone = '') AS empty_phone")
        if "ip_region" in columns:
            parts.append("SUM(ip_region IS NOT NULL AND ip_region <> '') AS filled_ip")
        if "phone_region" in columns:
            parts.append("SUM(phone_region IS NOT NULL AND phone_region <> '') AS filled_phone")
        row = (await db.execute(text(f"SELECT {', '.join(parts)} FROM users"))).mappings().first()
        if row:
            data = dict(row)
            for key in ("total", "empty_ip", "empty_phone", "filled_ip", "filled_phone"):
                result[key] = int(data.get(key) or 0)
        return result


def _readiness(payload: dict[str, Any]) -> dict[str, bool]:
    provider = payload.get("provider")
    ip = bool(
        provider == "offline"
        and payload["ip"]["exists"]
        and payload["ip"]["size_bytes"] > 0
        and payload["ip"]["binding_available"]
    )
    phone = bool(
        provider == "offline"
        and payload["phone"]["exists"]
        and payload["phone"]["size_bytes"] > 0
        and payload["phone"]["header_ok"]
        and payload["phone"]["rows"] > 0
    )
    if payload.get("db_checked"):
        db = payload.get("db") or {}
        if not db.get("available"):
            return {"ip": False, "phone": False}
        columns = db.get("columns") or {}
        ip = ip and columns.get("ip_region", False) and columns.get("register_ip", False)
        phone = phone and columns.get("phone_region", False) and columns.get("phone", False)
    return {"ip": ip, "phone": phone}


def collect(*, skip_db: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": registry.provider(),
        "ip": _ip_status(),
        "phone": _phone_segment_status(phone_offline.resolve_segment_path()),
        "db_checked": not skip_db,
        "db": None,
    }
    if not skip_db:
        try:
            payload["db"] = asyncio.run(_db_status())
        except Exception as exc:  # 预检失败要能被 --expect-ready 识别，而不是抛栈
            payload["db"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    payload["ready"] = _readiness(payload)
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    ip = payload["ip"]
    phone = payload["phone"]
    db = payload.get("db")
    print(f"provider: {payload['provider']}")
    print(
        f"ip_db: exists={ip['exists']} size={ip['size_bytes']} "
        f"binding={ip['binding_available']} sha256={ip['sha256']}"
    )
    print(
        f"phone_segment: exists={phone['exists']} size={phone['size_bytes']} "
        f"header_ok={phone['header_ok']} rows={phone['rows']} "
        f"max_updated_at={phone['max_updated_at']}"
    )
    if payload.get("db_checked"):
        print(f"db: {db}")
    print(f"ready: ip={payload['ready']['ip']} phone={payload['ready']['phone']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A12 region refresh readiness check (read-only)")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--skip-db", action="store_true", help="跳过数据库检查（CI 单元测试）")
    parser.add_argument(
        "--expect-ready",
        action="store_true",
        help="IP 与手机任一未就绪时以退出码 2 结束",
    )
    args = parser.parse_args(argv)

    payload = collect(skip_db=args.skip_db)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(payload)
    if args.expect_ready and not all(payload["ready"].values()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
