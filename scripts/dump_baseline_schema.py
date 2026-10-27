#!/usr/bin/env python
"""Export the schema of a **verified** database as ``migrations/baseline/schema.sql``.

baseline 的用途
    新环境与 CI 不再依赖 ``database_setup_marriage.py`` 的隐藏补列：先用 baseline 建出
    "某个已验证版本"的完整结构，再按 manifest 顺序执行增量迁移，结果必须与代码契约一致
    （见 ``scripts/schema_contract.py`` 与 ``.github/workflows/schema-contract.yml``）。

安全边界
    1. 只读：只执行 ``information_schema`` 查询与 ``SHOW CREATE TABLE``；
    2. 必须显式 ``--confirm-database <库名>``，且库名包含 prod/production 时拒绝，
       避免对生产库误生成/覆盖；
    3. 输出文件不会被自动应用，是否导入由发布窗口人工决定。

用法
    python scripts/dump_baseline_schema.py \
        --database-url mysql://root:pw@127.0.0.1:3306/xuanshiai_verify \
        --confirm-database xuanshiai_verify
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "migrations" / "baseline" / "schema.sql"
_PROD_HINTS = ("prod", "production", "live")


def _connect(database_url: str):
    import pymysql

    parsed = urlparse(database_url)
    if parsed.scheme.split("+")[0] not in {"mysql", "mariadb"}:
        raise ValueError(f"unsupported database url scheme: {parsed.scheme}")
    database = parsed.path.lstrip("/")
    if not database:
        raise ValueError("database url must include a database name")
    connection = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=unquote(parsed.username or "root"),
        password=unquote(parsed.password or ""),
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    return connection, database


def _normalize_create(statement: str) -> str:
    """Drop per-environment noise so the baseline diff is stable."""
    text = statement.strip().rstrip(";")
    text = re.sub(r"\s+AUTO_INCREMENT=\d+", "", text)
    return text + ";"


def dump(database_url: str, confirm_database: str, out: Path) -> int:
    connection, database = _connect(database_url)
    if confirm_database != database:
        raise SystemExit(f"--confirm-database {confirm_database!r} does not match target {database!r}")
    if any(hint in database.lower() for hint in _PROD_HINTS):
        raise SystemExit(f"refusing to dump production-looking database {database!r}")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME AS table_name FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
                (database,),
            )
            tables = [str(row["table_name"]) for row in cursor.fetchall()]
            statements = []
            for table in tables:
                cursor.execute(f"SHOW CREATE TABLE `{table}`")
                row = cursor.fetchone()
                create = list(row.values())[1] if isinstance(row, dict) else row[1]
                statements.append(_normalize_create(str(create)))
    finally:
        connection.close()

    header = [
        "-- baseline schema（由 scripts/dump_baseline_schema.py 生成，勿手改）",
        f"-- source database: {database}",
        f"-- tables: {len(tables)}",
        "-- 生成后请执行：python scripts/schema_contract.py check-db 验证与代码契约一致。",
        "",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
    ]
    payload = "\n".join(header) + "\n\n" + "\n\n".join(statements) + "\n\nSET FOREIGN_KEY_CHECKS = 1;\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.parent / "SHA256SUMS").write_text(f"{digest}  {out.name}\n", encoding="utf-8", newline="\n")
    print(f"baseline written: {out} ({len(tables)} tables, sha256={digest})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a verified database schema as the baseline")
    parser.add_argument("--database-url", required=True, help="mysql://user:pass@host:port/db")
    parser.add_argument("--confirm-database", required=True, help="must equal the database name in the URL")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    return dump(args.database_url, args.confirm_database, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())