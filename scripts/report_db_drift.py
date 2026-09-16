#!/usr/bin/env python
"""生产库结构缺口体检（只读）。

背景
    ``database_setup_marriage.py`` 里 ``_ensure_table_columns`` 的 ALTER 失败会被
    ``except Exception`` 降级成一条 WARNING 日志（提交 8ec8ff1 引入的列定义缺少
    ``字段名`` 前缀就是这样被吞掉的），由此产生的“列没建上”只能等应用抛 1054 才被发现。
    生产环境按 README 约定 ``AUTO_INIT_DB=false``，启动不会跑初始化脚本，缺列会在发布后
    才暴露成 500。

本脚本做什么
    以“只读演练”的方式跑一遍 ``DatabaseManager.init_all_tables()``：
    所有 SHOW / SELECT 真正执行（拿到真实库状态），所有 CREATE / ALTER / INSERT / UPDATE
    只被记录、**绝不执行**。于是记录下来的语句，就是“在生产库上还缺失、需要执行的 DDL/DML”。

    因此它不会改动任何数据，可以随时对生产库执行。

用法
    python scripts/report_db_drift.py             # 摘要
    python scripts/report_db_drift.py --verbose   # 打印全部待执行语句

退出码
    0 = 库结构与代码一致；1 = 存在缺口；2 = 连接失败。
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql  # noqa: E402

from database_setup_marriage import DatabaseManager, get_db_config  # noqa: E402

# 只读语句前缀：这些必须真正执行，才能拿到库的真实状态。
_READ_PREFIXES = ("SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN", "WITH", "USE")

_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?([A-Za-z0-9_]+)`?", re.IGNORECASE
)
_ALTER_TABLE_RE = re.compile(r"^\s*ALTER\s+TABLE\s+`?([A-Za-z0-9_]+)`?\s+(.*)$", re.IGNORECASE | re.DOTALL)
_ADD_COLUMN_RE = re.compile(r"ADD\s+COLUMN\s+`?([A-Za-z0-9_]+)`?", re.IGNORECASE)
_ADD_INDEX_RE = re.compile(r"ADD\s+(?:UNIQUE\s+)?KEY\s+`?([A-Za-z0-9_]+)`?", re.IGNORECASE)


class _ReadOnlyCursor:
    """把写语句记下来、只让读语句落到真实库的游标包装。

    靶库里不存在的表会让 ``SHOW COLUMNS`` / ``SELECT`` 抛 1146；在真实初始化流程里这些表
    是刚刚被 CREATE 出来的，因此不会出错。只读演练不会建表，所以这里把读语句的
    ``ProgrammingError`` 降级为“空结果集”，让初始化流程按“表缺失→跳过”继续跑完，
    缺口则由被记录的 CREATE 语句体现。
    """

    def __init__(self, real) -> None:
        self._real = real
        self.recorded: list[tuple[str, object]] = []
        self._last_was_write = False
        self._empty_result = False
        self.skipped_reads = 0

    # ---- 写拦截 ----
    def execute(self, sql, args=None):
        head = sql.lstrip().split(None, 1)[0].upper() if sql and sql.strip() else ""
        if head in _READ_PREFIXES:
            self._last_was_write = False
            try:
                return self._real.execute(sql, args)
            except pymysql.err.ProgrammingError:
                # 靶库缺表/缺列导致的只读失败：按空结果处理，继续演练。
                self.skipped_reads += 1
                self._empty_result = True
                return 0
        self._last_was_write = True
        self.recorded.append((sql, args))
        return 0

    def executemany(self, sql, args=None):
        head = sql.lstrip().split(None, 1)[0].upper() if sql and sql.strip() else ""
        if head in _READ_PREFIXES:
            self._last_was_write = False
            try:
                return self._real.executemany(sql, args)
            except pymysql.err.ProgrammingError:
                self.skipped_reads += 1
                self._empty_result = True
                return 0
        self._last_was_write = True
        self.recorded.append((sql, args))
        return 0

    # ---- 写语句的“影响行数”一律视为 0，让回填类 while 循环立即收敛 ----
    @property
    def rowcount(self) -> int:
        if self._last_was_write:
            return 0
        return self._real.rowcount

    @property
    def lastrowid(self):
        return None if self._last_was_write else self._real.lastrowid

    # ---- 其余全部转发给真实游标 ----
    def __getattr__(self, item):
        return getattr(self._real, item)

    def fetchone(self):
        return None if self._empty_result else self._real.fetchone()

    def fetchall(self):
        return [] if self._empty_result else self._real.fetchall()

    def fetchmany(self, size=None):  # pragma: no cover - 依赖第三方调用形态
        if self._empty_result:
            return []
        return self._real.fetchmany(size) if size else self._real.fetchmany()

    def nextset(self):
        return None if self._empty_result else self._real.nextset()

    def __iter__(self):
        return iter(self.fetchall())



def _table_exists(cursor, table: str, cache: dict[str, bool]) -> bool:
    if table not in cache:
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        row = cursor.fetchone()
        cache[table] = bool(row and list(row.values())[0])
    return cache[table]


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查目标库相对代码缺了哪些表/列/索引")
    parser.add_argument("--verbose", action="store_true", help="打印全部待执行语句")
    args = parser.parse_args()

    # 演练过程会照常打日志（含“表不存在”之类的噪声），这里只保留 ERROR。
    logging.getLogger("database_setup_marriage").setLevel(logging.ERROR)

    cfg = get_db_config()
    target = f"{cfg.get('host')}:{cfg.get('port')}/{cfg.get('database')}"
    print(f"=== 结构缺口体检（只读）目标库：{target} ===")

    try:
        conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor)
    except Exception as exc:  # noqa: BLE001
        print(f"连接失败：{exc}")
        return 2

    exists_cache: dict[str, bool] = {}
    try:
        with conn.cursor() as real_cursor:
            recorder = _ReadOnlyCursor(real_cursor)
            DatabaseManager.__new__(DatabaseManager).init_all_tables(recorder)

            missing_tables: list[str] = []
            missing_columns: list[str] = []
            missing_indexes: list[str] = []
            other: list[str] = []

            for sql, _params in recorder.recorded:
                create_match = _CREATE_TABLE_RE.match(sql)
                if create_match:
                    table = create_match.group(1)
                    if not _table_exists(real_cursor, table, exists_cache):
                        missing_tables.append(table)
                    continue

                alter_match = _ALTER_TABLE_RE.match(sql)
                if alter_match:
                    table, tail = alter_match.group(1), alter_match.group(2)
                    # 表本身都不在的库（从未跑过建表链路），列/索引缺口没有意义，
                    # 统一归到“其他语句”，避免报告里出现误导性的列名。
                    if _table_exists(real_cursor, table, exists_cache):
                        column_match = _ADD_COLUMN_RE.search(tail)
                        index_match = _ADD_INDEX_RE.search(tail)
                        if column_match:
                            missing_columns.append(f"{table}.{column_match.group(1)}")
                            continue
                        if index_match:
                            missing_indexes.append(f"{table}.{index_match.group(1)}")
                            continue
                    other.append(sql)
                    continue

                other.append(sql)
    finally:
        conn.rollback()
        conn.close()

    print()
    print(f"[1] 缺失的表     {len(missing_tables)}")
    for item in sorted(missing_tables):
        print(f"    - {item}")
    print(f"[2] 缺失的列     {len(missing_columns)}")
    for item in sorted(missing_columns):
        print(f"    - {item}")
    print(f"[3] 缺失的索引   {len(missing_indexes)}")
    for item in sorted(missing_indexes):
        print(f"    - {item}")
    print(f"[4] 其他待执行语句（MODIFY / 种子 INSERT IGNORE / 回填 UPDATE / 外键等）  {len(other)}")

    if args.verbose:
        print()
        print("--- 其他待执行语句明细 ---")
        for sql in other:
            print("    " + " ".join(sql.split()))

    gaps = len(missing_tables) + len(missing_columns) + len(missing_indexes)
    print()
    if not gaps:
        print("结论：未发现结构性缺口（表/列/索引均与代码一致）。")
        return 0
    print(f"结论：发现 {gaps} 处结构性缺口（另有 {len(other)} 条非结构性语句待执行）。")
    print("补齐方式（二选一，均在发布窗口 + 备份后执行）：")
    print("  1) python database_setup_marriage.py   # 幂等，按代码全量对齐（推荐）")
    print("  2) 按 migrations/m4_m7/ 下的定向迁移脚本手工补列")
    return 1



if __name__ == "__main__":
    raise SystemExit(main())
