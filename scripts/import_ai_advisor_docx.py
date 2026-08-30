"""Import relationship-advisor phrases from the source DOCX into MySQL."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree
from zipfile import ZipFile

import pymysql
from sqlalchemy.engine import make_url

from app.core.config import settings


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SECTION_PATTERN = re.compile(r"^[一二三四五六七八九十]+、")
SECTION_MAP = {
    "一、初次开场话术": ("opening", "初次开场"),
    "二、话题延伸话术": ("topic_extension", "话题延伸"),
    "三、日常关心类话术": ("care", "日常关心"),
    "四、夸赞话术": ("compliment", "真诚夸赞"),
    "五、价值观&生活观念聊天话术": ("values", "价值观与生活观念"),
    "六、幽默轻松调剂话术": ("rescue", "幽默轻松调剂"),
    "七、冷场救场万能话术": ("rescue", "冷场救场"),
    "八、拉近关系、增加暧昧感话术": ("intimacy", "关系推进"),
    "九、收尾结束语术": ("closing", "自然收尾"),
    "十、沟通避雷规则": ("analyze", "沟通避雷"),
}


def iter_paragraphs(path: Path) -> Iterator[str]:
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = {"w": W_NS}
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            yield text


def extract_entries(path: Path) -> list[tuple[str, str, str, str]]:
    entries: list[tuple[str, str, str, str]] = []
    current: tuple[str, str] | None = None
    for paragraph in iter_paragraphs(path):
        if SECTION_PATTERN.match(paragraph):
            current = next((value for heading, value in SECTION_MAP.items() if paragraph.startswith(heading)), None)
            continue
        if current is None or paragraph in {"前言", "说明：全部为可直接复制使用话术，按场景分类"}:
            continue
        scenario, category = current
        entries.append((scenario, category, paragraph, _tone_for(scenario, category)))
    return entries


def _tone_for(scenario: str, category: str) -> str:
    if category == "幽默轻松调剂":
        return "humorous"
    if scenario in {"care", "compliment", "intimacy"}:
        return "warm"
    if scenario in {"values", "analyze"}:
        return "mature"
    if category == "冷场救场":
        return "natural"
    return "natural"


def connect() -> pymysql.Connection:
    database_url = make_url(settings.database_url)
    if database_url.drivername not in {"mysql", "mysql+pymysql", "mysql+aiomysql"}:
        raise SystemExit(f"Unsupported database URL: {database_url.drivername}")
    if not database_url.host or not database_url.username or not database_url.database:
        raise SystemExit("DATABASE_URL must include host, username, and database name")
    return pymysql.connect(
        host=database_url.host,
        port=database_url.port or 3306,
        user=database_url.username,
        password=database_url.password or "",
        database=database_url.database,
        charset="utf8mb4",
        autocommit=False,
    )


def import_entries(path: Path, version: str) -> tuple[int, int]:
    entries = extract_entries(path)
    if not entries:
        raise SystemExit(f"No importable phrases found in {path}")
    inserted = 0
    skipped = 0
    source = path.name
    connection = connect()
    try:
        with connection.cursor() as cursor:
            for scenario, category, content, tone in entries:
                cursor.execute(
                    """SELECT id FROM ai_advisor_knowledge
                    WHERE advisor_type='relationship' AND scenario=%s AND tone=%s
                      AND content=%s AND version=%s LIMIT 1""",
                    (scenario, tone, content, version),
                )
                if cursor.fetchone():
                    skipped += 1
                    continue
                cursor.execute(
                    """INSERT INTO ai_advisor_knowledge
                    (advisor_type, category, scenario, relationship_stage, tone,
                     content, reason, risk_level, source, version, enabled)
                    VALUES ('relationship', %s, %s, 'new', %s, %s, %s, 'low', %s, %s, 1)""",
                    (
                        category,
                        scenario,
                        tone,
                        content,
                        f"来自《{source}》的{category}话术，按原文保留，用于辅助生成自然沟通建议。",
                        source,
                        version,
                    ),
                )
                inserted += 1
        connection.commit()
    finally:
        connection.close()
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path("no upload/情感话术.docx"))
    parser.add_argument("--version", default="docx-v1")
    args = parser.parse_args()
    if settings.environment not in {"development", "testing"}:
        raise SystemExit("Refusing to import DOCX outside development/testing environment")
    inserted, skipped = import_entries(args.path, args.version)
    print(f"source={args.path}")
    print(f"version={args.version}")
    print(f"inserted={inserted}")
    print(f"skipped={skipped}")


if __name__ == "__main__":
    main()


