"""Seed relationship-advisor knowledge for local or deployment databases."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


_SEEDS = (
    ("opening", "开场", "先用轻松自然的问候打开话题，不急于索取私人信息。"),
    ("reply", "回复", "先回应对方消息中的具体内容，再补充一个容易回答的问题。"),
    ("topic_extension", "话题延伸", "围绕对方已经表达的兴趣继续展开，避免连续查户口式提问。"),
    ("rescue", "冷场救场", "切换到轻松的共同话题，降低压力，不用追问对方为什么不回复。"),
    ("care", "日常关心", "表达具体而克制的关心，同时尊重对方的时间和回复节奏。"),
    ("compliment", "真诚夸赞", "夸赞具体行为或感受，避免外貌评价、夸大承诺和让对方有负担的表达。"),
    ("values", "价值观", "用开放问题了解生活观念，不把一次回答包装成确定的匹配结论。"),
    ("intimacy", "关系推进", "在互动舒适且有回应时表达欣赏，不使用试探、操控或强迫式话术。"),
    ("closing", "收尾", "自然说明要结束聊天，留下下次继续交流的空间，不要求对方立即承诺。"),
    ("analyze", "沟通分析", "观察回应长度、具体程度和主动性，只给出可能性分析，不下绝对结论。"),
)
_TONES = ("natural", "warm", "humorous", "mature")


def main() -> None:
    if settings.environment not in {"development", "testing"} and os.getenv("ALLOW_AI_ADVISOR_SEED") != "1":
        raise SystemExit("Refusing to seed outside development/testing; set ALLOW_AI_ADVISOR_SEED=1 for an explicit deployment.")
    connection = pymysql.connect(
        host=settings.database_host,
        port=settings.database_port,
        user=settings.database_user,
        password=settings.database_password.get_secret_value(),
        database=settings.database_name,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        with connection.cursor() as cursor:
            for scenario, category, guidance in _SEEDS:
                for tone in _TONES:
                    content = f"{guidance} 语气：{tone}。"
                    cursor.execute("""SELECT id FROM ai_advisor_knowledge
                        WHERE advisor_type='relationship' AND scenario=%s AND tone=%s AND version='seed-v2' LIMIT 1""", (scenario, tone))
                    if cursor.fetchone():
                        continue
                    cursor.execute("""INSERT INTO ai_advisor_knowledge
                        (advisor_type, category, scenario, relationship_stage, tone, content, reason, risk_level, source, version, enabled)
                        VALUES ('relationship', %s, %s, 'new', %s, %s, %s, 'low', '情感话术整理（待授权核验）', 'seed-v2', 1)""", (category, scenario, tone, content, guidance))
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    main()

