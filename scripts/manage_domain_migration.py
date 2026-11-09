#!/usr/bin/env python
"""Apply reviewed migrations for the paper_plane and chat domains.

The business and AI domains have dedicated runners. paper_plane and chat had
manifest files (or only READMEs) but no executable path, so their migrations
were "written but never run". This runner uses the same manifest/checksum
shape as ``manage_business_migration.py`` and records every attempt in
``domain_schema_migration``.

Usage:
    python scripts/manage_domain_migration.py status --domain chat --target development
    python scripts/manage_domain_migration.py up --domain paper_plane --target test
    python scripts/manage_domain_migration.py verify --domain chat --target test

Exit codes:
    0 success; 1 migration/verification failed; 2 configuration/connection error
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import pymysql
from pymysql.cursors import DictCursor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from manage_business_migration import _database_config_for_target  # noqa: E402

MIGRATIONS_ROOT = ROOT / "migrations"
ALLOWED_DOMAINS = frozenset({"paper_plane", "chat"})
IGNORABLE_MYSQL_ERRORS = {1050, 1060, 1061, 1091}
SAFE_DOMAIN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
VERIFY_MODES = frozenset({"empty", "info"})


class MigrationError(RuntimeError):
    """Raised for an unsafe or incomplete migration operation."""


def _manifest_path(domain: str) -> Path:
    if domain not in ALLOWED_DOMAINS or not SAFE_DOMAIN.fullmatch(domain):
        raise MigrationError(f"unsupported migration domain: {domain}")
    path = MIGRATIONS_ROOT / domain / "manifest.json"
    if not path.is_file():
        raise MigrationError(f"missing manifest: {path}")
    return path


def _checksum(path: Path, expected: str) -> None:
    if not expected:
        return
    digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if expected != digest:
        raise MigrationError(f"migration checksum mismatch: {path.name}")


def _manifest(domain: str) -> list[dict[str, Any]]:
    path = _manifest_path(domain)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read manifest {path}: {exc}") from exc
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        raise MigrationError(f"manifest {path} has no versions")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for entry in versions:
        if not isinstance(entry, dict):
            raise MigrationError(f"manifest {path} has a non-object version")
        version = str(entry.get("version", "")).strip()
        if not version or version in seen:
            raise MigrationError(f"manifest {path} has an empty/duplicate version: {version!r}")
        seen.add(version)
        for key in ("up", "down"):
            name = str(entry.get(key, "")).strip()
            if not name or Path(name).name != name:
                raise MigrationError(f"{domain}.{version}.{key} must be a plain filename")
            target = MIGRATIONS_ROOT / domain / name
            if not target.is_file():
                raise MigrationError(f"{domain}.{version}.{key} file missing: {name}")
            _checksum(target, str(entry.get("sha256", {}).get(key, "")))
        for key in ("verify", "verify_previous"):
            name = str(entry.get(key, "") or "").strip()
            if name:
                target = MIGRATIONS_ROOT / domain / name
                if not target.is_file():
                    raise MigrationError(f"{domain}.{version}.{key} file missing: {name}")
        mode = str(entry.get("verify_mode", "empty")).strip()
        if mode not in VERIFY_MODES:
            raise MigrationError(f"{domain}.{version}.verify_mode must be one of {sorted(VERIFY_MODES)}")
        item = dict(entry)
        item["verify_mode"] = mode
        normalized.append(item)
    return normalized


def _statements(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if "DELIMITER" in text.upper():
        raise MigrationError(f"{path.name}: DELIMITER is not supported by this runner")
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("--"):
            continue
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if statement:
                chunks.append(statement)
            current = []
    trailing = "\n".join(current).strip()
    if trailing:
        chunks.append(trailing)
    return chunks


def _connection_config(target: str, database_url: str | None) -> dict[str, Any]:
    if database_url:
        parsed = urlsplit(database_url.replace("mysql+aiomysql://", "mysql://", 1))
        if parsed.scheme != "mysql" or not parsed.hostname or not parsed.port:
            raise MigrationError("--database-url must be a complete mysql://host:port/database URL")
        expected_database = unquote(parsed.path.lstrip("/"))
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        try:
            from database_setup_marriage import get_db_config

            config = get_db_config()
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous
        if str(config.get("database")) != expected_database:
            raise MigrationError("--database-url did not resolve to the requested database")
        return config
    return _database_config_for_target(target)


def _connect(target: str, database_url: str | None) -> tuple[pymysql.connections.Connection, str]:
    config = _connection_config(target, database_url)
    database = str(config.get("database") or "")
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise MigrationError("invalid database identifier")
    return pymysql.connect(**config, cursorclass=DictCursor, autocommit=False), database


def _ensure_history(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS `domain_schema_migration` (
            `domain` varchar(32) NOT NULL,
            `version` varchar(128) NOT NULL,
            `checksum` char(64) NOT NULL,
            `status` varchar(24) NOT NULL,
            `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `finished_at` datetime DEFAULT NULL,
            `last_step` int unsigned NOT NULL DEFAULT 0,
            `error_message` varchar(1000) DEFAULT NULL,
            PRIMARY KEY (`domain`, `version`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def _lock(cursor: Any, domain: str) -> None:
    cursor.execute("SELECT GET_LOCK(%s, 30) AS acquired", (f"xuanshiai_{domain}_schema_migration_v1",))
    if int((cursor.fetchone() or {}).get("acquired") or 0) != 1:
        raise MigrationError(f"could not acquire {domain} migration advisory lock")


def _unlock(cursor: Any, domain: str) -> None:
    cursor.execute("SELECT RELEASE_LOCK(%s)", (f"xuanshiai_{domain}_schema_migration_v1",))


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _record_start(cursor: Any, domain: str, entry: dict[str, Any], status: str) -> None:
    checksum = str(entry.get("sha256", {}).get("up", "")) or _digest(
        MIGRATIONS_ROOT / domain / str(entry["up"])
    )
    cursor.execute(
        """INSERT INTO domain_schema_migration
           (domain, version, checksum, status, started_at, finished_at, last_step, error_message)
           VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), NULL, 0, NULL)
           ON DUPLICATE KEY UPDATE checksum=VALUES(checksum), status=VALUES(status),
             started_at=UTC_TIMESTAMP(), finished_at=NULL, last_step=0, error_message=NULL""",
        (domain, str(entry["version"]), checksum, status),
    )


def _record_finish(cursor: Any, domain: str, version: str, status: str, step: int = 0, error: str | None = None) -> None:
    cursor.execute(
        """UPDATE domain_schema_migration
           SET status=%s, finished_at=UTC_TIMESTAMP(), last_step=%s, error_message=%s
           WHERE domain=%s AND version=%s""",
        (status, step, error, domain, version),
    )


def _execute_file(cursor: Any, path: Path) -> int:
    statements = _statements(path)
    for statement in statements:
        try:
            cursor.execute(statement)
        except pymysql.MySQLError as exc:
            code = int(exc.args[0]) if exc.args and isinstance(exc.args[0], int) else 0
            if code not in IGNORABLE_MYSQL_ERRORS:
                raise
    return len(statements)


def _run_verify(cursor: Any, domain: str, entry: dict[str, Any], key: str) -> list[Any] | None:
    name = str(entry.get(key, "") or "").strip()
    if not name:
        return None
    statements = _statements(MIGRATIONS_ROOT / domain / name)
    if entry["verify_mode"] == "empty" and len(statements) != 1:
        raise MigrationError(f"{domain}.{entry['version']}.{key} must contain exactly one query")
    rows: list[Any] = []
    for statement in statements:
        cursor.execute(statement)
        rows.extend(cursor.fetchall())
    if entry["verify_mode"] == "empty" and rows:
        raise MigrationError(f"{domain}.{entry['version']}.{key} returned {len(rows)} row(s)")
    if entry["verify_mode"] == "info":
        print(f"verify_info domain={domain} version={entry['version']} rows={len(rows)}")
    return rows


def _selected(versions: list[dict[str, Any]], selected: list[str] | None) -> list[dict[str, Any]]:
    if not selected:
        return versions
    wanted = set(selected)
    known = {str(item["version"]) for item in versions}
    missing = sorted(wanted - known)
    if missing:
        raise MigrationError(f"unknown versions: {', '.join(missing)}")
    return [item for item in versions if str(item["version"]) in wanted]


def _run(command: str, domain: str, target: str, database_url: str | None, selected: list[str] | None) -> int:
    versions = _selected(_manifest(domain), selected)
    connection, database = _connect(target, database_url)
    try:
        with connection.cursor() as cursor:
            _lock(cursor, domain)
            try:
                _ensure_history(cursor)
                connection.commit()
                if command == "status":
                    for entry in versions:
                        cursor.execute(
                            "SELECT status, started_at, finished_at, error_message "
                            "FROM domain_schema_migration WHERE domain=%s AND version=%s",
                            (domain, entry["version"]),
                        )
                        row = cursor.fetchone()
                        print(
                            f"{entry['version']}: {(row or {}).get('status', 'not_started')} "
                            f"mode={entry['verify_mode']}"
                        )
                    return 0
                if command == "verify":
                    for entry in versions:
                        _run_verify(cursor, domain, entry, "verify")
                    print(f"verify domain={domain} database={database} versions={len(versions)} status=OK")
                    return 0
                if command == "up":
                    for entry in versions:
                        version = str(entry["version"])
                        _record_start(cursor, domain, entry, "running")
                        connection.commit()
                        try:
                            step = _execute_file(cursor, MIGRATIONS_ROOT / domain / str(entry["up"]))
                            _run_verify(cursor, domain, entry, "verify")
                            _record_finish(cursor, domain, version, "succeeded", step)
                            connection.commit()
                        except Exception as exc:
                            connection.rollback()
                            _record_finish(cursor, domain, version, "failed", 0, type(exc).__name__)
                            connection.commit()
                            raise
                    print(f"migration domain={domain} database={database} status=succeeded")
                    return 0
                if command == "down":
                    for entry in reversed(versions):
                        version = str(entry["version"])
                        _record_start(cursor, domain, entry, "rolling_back")
                        connection.commit()
                        try:
                            step = _execute_file(cursor, MIGRATIONS_ROOT / domain / str(entry["down"]))
                            _run_verify(cursor, domain, entry, "verify_previous")
                            _record_finish(cursor, domain, version, "rolled_back", step)
                            connection.commit()
                        except Exception as exc:
                            connection.rollback()
                            _record_finish(cursor, domain, version, "rollback_failed", 0, type(exc).__name__)
                            connection.commit()
                            raise
                    print(f"migration domain={domain} database={database} status=rolled_back")
                    return 0
                raise MigrationError(f"unknown command: {command}")
            finally:
                _unlock(cursor, domain)
    finally:
        connection.close()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage paper_plane/chat reviewed migrations")
    parser.add_argument("command", choices=("status", "up", "down", "verify"))
    parser.add_argument("--domain", required=True, choices=sorted(ALLOWED_DOMAINS))
    parser.add_argument("--target", default="development", choices=("development", "test"))
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--version", action="append", default=None, help="only this manifest version")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.database_url and not args.allow_production:
        print("migration failed: --database-url requires --allow-production", file=sys.stderr)
        return 2
    try:
        return _run(args.command, args.domain, args.target, args.database_url, args.version)
    except (MigrationError, pymysql.MySQLError) as exc:
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
