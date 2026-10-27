#!/usr/bin/env python
"""Schema contract: derive the schema the code promises, then diff a live MySQL database.

为什么需要它
    ``database_setup_marriage.py`` 的 bootstrap 由三段拼出来：``CREATE TABLE``（4 个模块）、
    ``_ensure_required_columns`` / ``_ensure_filter_columns`` 的隐藏补列、``_ensure_optional_index``
    的补索引。其中补列失败只打 warning（提交 8ec8ff1 的事故），生产 ``AUTO_INIT_DB=false``
    时又不会跑 bootstrap，缺列只能等 500 才暴露。

本脚本做什么
    1. 从代码解析"应该存在"的表/列/索引（含隐藏补列）；
    2. 从目标库读 ``information_schema`` 得到"实际存在"；
    3. 只报告"应该有但没有 / 类型不一致"，多出来的对象只做提示（迁移和 bootstrap 会加更多东西）。

用法
    python scripts/schema_contract.py check-db --database-url mysql://root:pw@127.0.0.1:3306/db
    DATABASE_URL=... python scripts/schema_contract.py check-db
    python scripts/schema_contract.py export --out migrations/schema_contract.json

退出码
    0 = 契约一致；1 = 发现缺口；2 = 无法解析或连接失败。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SOURCES = (
    "database_setup_marriage.py",
    "app/db/business_schema.py",
    "app/db/ai_schema.py",
    "app/db/derivation_schema.py",
)

_CREATE_RE = re.compile(r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+`?([A-Za-z0-9_]+)`?\s*\(", re.I)
_COLUMN_RE = re.compile(r"^\s*`?([A-Za-z0-9_]+)`?\s+([A-Za-z]+(?:\([^)]*\))?(?:\s+unsigned)?)", re.I)
_INDEX_RE = re.compile(r"^\s*(UNIQUE\s+KEY|KEY|PRIMARY\s+KEY|INDEX)\s*`?([A-Za-z0-9_]*)`?\s*\(([^)]*)\)", re.I)
_SKIP_HEADS = {"primary", "unique", "key", "index", "constraint", "check", "foreign", "fulltext", "spatial"}
_INT_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


_TYPE_WORDS = (
    "bigint", "int", "tinyint", "smallint", "mediumint", "varchar", "char", "text", "longtext",
    "mediumtext", "decimal", "numeric", "datetime", "date", "time", "timestamp", "json", "enum",
    "set", "blob", "float", "double", "bool", "boolean", "bit",
)


def _is_type_token(token: str) -> bool:
    return any(token == word or token.startswith(f"{word}(") for word in _TYPE_WORDS)


def normalize_type(column_type: str) -> str:
    """Normalize DDL/COLUMN_TYPE spellings so ``bigint(20) unsigned`` == ``bigint unsigned``.

    Also drops the column name and constraints (``NOT NULL``/``DEFAULT``/``COMMENT`` ...) so a
    full DDL definition and ``information_schema.COLUMN_TYPE`` compare on the same footing.
    """
    tokens = " ".join(str(column_type).strip().lower().replace("`", "").split()).split()
    if len(tokens) > 1 and not _is_type_token(tokens[0]) and _is_type_token(tokens[1]):
        tokens = tokens[1:]
    text = " ".join(tokens)
    match = re.match(r"^([a-z]+(?:\([^)]*\))?(?:\s+unsigned)?(?:\s+zerofill)?)", text)
    if match:
        text = match.group(1)
    text = _INT_WIDTH_RE.sub(r"\1", text)
    # MySQL 8 reports enum/set members with escaped quotes; keep the payload, drop the noise.
    text = text.replace("\\'", "'")
    return text


def _parse_ddl(text: str) -> dict[str, dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    pos = 0
    while True:
        match = _CREATE_RE.search(text, pos)
        if not match:
            break
        depth, cursor = 1, match.end()
        while cursor < len(text) and depth:
            if text[cursor] == "(":
                depth += 1
            elif text[cursor] == ")":
                depth -= 1
            cursor += 1
        body = text[match.end() : cursor - 1]
        columns: dict[str, str] = {}
        indexes: list[dict[str, Any]] = []
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            index_match = _INDEX_RE.match(line)
            if index_match:
                kind = " ".join(index_match.group(1).upper().split())
                if kind == "PRIMARY KEY":
                    continue
                indexes.append(
                    {
                        "name": index_match.group(2),
                        "unique": kind.startswith("UNIQUE"),
                        "columns": [part.strip() for part in index_match.group(3).replace("`", "").split(",")],
                    }
                )
                continue
            if line.split(None, 1)[0].lower() in _SKIP_HEADS:
                continue
            column_match = _COLUMN_RE.match(line)
            if column_match:
                columns[column_match.group(1)] = normalize_type(column_match.group(2))
        tables[match.group(1)] = {
            "columns": columns,
            "indexes": indexes,
            "has_primary_key": bool(re.search(r"PRIMARY\s+KEY", body, re.I)),
        }
        pos = match.end()
    return tables


def parse_code_schema(root: Path | None = None) -> dict[str, dict[str, Any]]:
    root = Path(root or ROOT)
    tables: dict[str, dict[str, Any]] = {}
    for relative in SCHEMA_SOURCES:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        for name, spec in _parse_ddl(path.read_text(encoding="utf-8", errors="replace")).items():
            spec["source"] = relative
            tables[name] = spec
    for table, columns in bootstrap_columns(root).items():
        target = tables.setdefault(table, {"columns": {}, "indexes": [], "has_primary_key": True, "source": "ensure"})
        for column, definition in columns:
            target["columns"].setdefault(column, normalize_type(definition.split("COMMENT")[0]))
    return tables


def _normalize_definition(definition: str) -> str:
    """Turn "`col` bigint unsigned NOT NULL COMMENT '...'" into "bigint unsigned not null"."""
    text = definition.strip()
    head = re.match(r"^`[A-Za-z0-9_]+`\s*", text)
    if head:
        text = text[head.end() :]
    text = text.split("COMMENT")[0].strip()
    return normalize_type(text)


def bootstrap_columns(root: Path | None = None) -> dict[str, list[tuple[str, str]]]:
    """Columns added by ``_ensure_required_columns`` and ``_ensure_filter_columns``."""
    root = Path(root or ROOT)
    tree = ast.parse((root / "database_setup_marriage.py").read_text(encoding="utf-8"))

    def literal(node: ast.AST, expected: type) -> Any:
        if isinstance(node, expected):
            return node.value
        raise ValueError(f"unsupported ensure definition: {ast.dump(node)[:80]}")

    result: dict[str, list[tuple[str, str]]] = {}

    def add(table: str, columns: dict[str, str]) -> None:
        bucket = result.setdefault(table.strip("`"), [])
        known = {name for name, _ in bucket}
        for name, definition in columns.items():
            if name not in known:
                bucket.append((name, definition))
                known.add(name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in {"_ensure_required_columns", "_ensure_filter_columns"}:
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr != "_ensure_table_columns" or len(call.args) < 3:
                continue
            try:
                table = literal(call.args[1], ast.Constant)
            except ValueError:
                # f"`{table_name}`" 之类的循环调用：对应表已由内联字典覆盖，跳过。
                continue
            mapping = call.args[2]
            if not isinstance(mapping, ast.Call) or not isinstance(mapping.func, ast.Name) or mapping.func.id != "_named_columns":
                continue
            columns = {literal(key, ast.Constant): literal(value, ast.Constant) for key, value in zip(mapping.args[0].keys, mapping.args[0].values)}  # type: ignore[attr-defined]
            add(table, columns)

        # 兼容 "table": {"col": "definition"} 的内联字典写法
        for dictionary in ast.walk(node):
            if not isinstance(dictionary, ast.Dict) or not dictionary.values:
                continue
            if not all(isinstance(value, ast.Dict) for value in dictionary.values):
                continue
            for table_node, column_dict in zip(dictionary.keys, dictionary.values):
                table = literal(table_node, ast.Constant)
                columns = {literal(key, ast.Constant): literal(value, ast.Constant) for key, value in zip(column_dict.keys, column_dict.values)}  # type: ignore[union-attr]
                add(table, columns)

    return result


def bootstrap_only_columns(root: Path | None = None) -> dict[str, list[tuple[str, str]]]:
    """Bootstrap columns that the base ``CREATE TABLE`` does not declare, per table."""
    root = Path(root or ROOT)
    base: dict[str, set[str]] = {}
    for relative in SCHEMA_SOURCES:
        for name, spec in _parse_ddl((root / relative).read_text(encoding="utf-8", errors="replace")).items():
            base.setdefault(name, set()).update(spec["columns"])
    result: dict[str, list[tuple[str, str]]] = {}
    for table, columns in bootstrap_columns(root).items():
        missing = [(name, definition) for name, definition in columns if name not in base.get(table, set())]
        if missing:
            result[table] = missing
    return result


def read_database_schema(connection: Any, database: str) -> dict[str, dict[str, Any]]:
    """Read the effective schema from ``information_schema`` (columns + indexes)."""
    tables: dict[str, dict[str, Any]] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME AS table_name FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
            (database,),
        )
        for row in cursor.fetchall():
            name = str(row["table_name"] if isinstance(row, dict) else row[0])
            tables[name] = {"columns": {}, "indexes": [], "has_primary_key": False, "source": "database"}
        cursor.execute(
            "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, COLUMN_TYPE AS column_type "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s",
            (database,),
        )
        for row in cursor.fetchall():
            item = row if isinstance(row, dict) else {"table_name": row[0], "column_name": row[1], "column_type": row[2]}
            tables.setdefault(item["table_name"], {"columns": {}, "indexes": [], "has_primary_key": False, "source": "database"})
            tables[item["table_name"]]["columns"][item["column_name"]] = normalize_type(item["column_type"])
        cursor.execute(
            "SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, NON_UNIQUE AS non_unique, "
            "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns "
            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s "
            "GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE",
            (database,),
        )
        for row in cursor.fetchall():
            item = row if isinstance(row, dict) else {"table_name": row[0], "index_name": row[1], "non_unique": row[2], "columns": row[3]}
            if item["index_name"] == "PRIMARY":
                tables.setdefault(item["table_name"], {"columns": {}, "indexes": [], "has_primary_key": False, "source": "database"})
                tables[item["table_name"]]["has_primary_key"] = True
                continue
            tables.setdefault(item["table_name"], {"columns": {}, "indexes": [], "has_primary_key": False, "source": "database"})
            tables[item["table_name"]]["indexes"].append(
                {
                    "name": item["index_name"],
                    "unique": not int(item["non_unique"]),
                    "columns": str(item["columns"]).split(","),
                }
            )
    return tables


def diff(expected: dict[str, dict[str, Any]], actual: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Only "should exist but does not" is a failure; extra objects are informational."""
    missing_tables = sorted(set(expected) - set(actual))
    missing_columns: list[str] = []
    type_mismatches: list[str] = []
    missing_indexes: list[str] = []
    extra_columns: list[str] = []
    missing_primary_keys: list[str] = []

    for table in sorted(set(expected) & set(actual)):
        want, have = expected[table], actual[table]
        for column, column_type in sorted(want["columns"].items()):
            if column not in have["columns"]:
                missing_columns.append(f"{table}.{column}")
            elif have["columns"][column] != column_type:
                type_mismatches.append(f"{table}.{column}: expected {column_type}, got {have['columns'][column]}")
        for column in sorted(set(have["columns"]) - set(want["columns"])):
            extra_columns.append(f"{table}.{column}")
        for index in want["indexes"]:
            if index["name"] and index["name"] not in {item["name"] for item in have["indexes"]}:
                missing_indexes.append(f"{table}.{index['name']}")
        if want.get("has_primary_key") and not have.get("has_primary_key"):
            missing_primary_keys.append(table)

    return {
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "type_mismatches": type_mismatches,
        "missing_indexes": missing_indexes,
        "missing_primary_keys": missing_primary_keys,
        "extra_columns": extra_columns,
    }


def connect(database_url: str) -> tuple[Any, str]:
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


def _print_report(report: dict[str, Any], verbose: bool) -> int:
    failures = (
        len(report["missing_tables"])
        + len(report["missing_columns"])
        + len(report["type_mismatches"])
        + len(report["missing_indexes"])
        + len(report["missing_primary_keys"])
    )
    print(f"missing tables        : {len(report['missing_tables'])}")
    print(f"missing columns       : {len(report['missing_columns'])}")
    print(f"type mismatches       : {len(report['type_mismatches'])}")
    print(f"missing indexes       : {len(report['missing_indexes'])}")
    print(f"missing primary keys  : {len(report['missing_primary_keys'])}")
    print(f"extra columns (info)  : {len(report['extra_columns'])}")
    if verbose or failures:
        for key in ("missing_tables", "missing_primary_keys", "missing_columns", "type_mismatches", "missing_indexes"):
            for item in report[key][:200]:
                print(f"  [{key}] {item}")
    print("contract: " + ("FAIL" if failures else "OK"))
    return 1 if failures else 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the live database against the schema the code promises")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="write the code-derived schema as JSON")
    export.add_argument("--out", default=str(ROOT / "migrations" / "schema_contract.json"))

    check = subparsers.add_parser("check-db", help="diff a live database against the code-derived schema")
    check.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    check.add_argument("--verbose", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "export":
        schema = parse_code_schema()
        Path(args.out).write_text(json.dumps({"tables": schema}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"exported {len(schema)} tables to {args.out}")
        return 0

    if not args.database_url:
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    connection, database = connect(args.database_url)
    try:
        actual = read_database_schema(connection, database)
    finally:
        connection.close()
    expected = parse_code_schema()
    print(f"schema contract: database={database} expected_tables={len(expected)} actual_tables={len(actual)}")
    return _print_report(diff(expected, actual), args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())