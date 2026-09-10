"""Backfill local demo users with distinct anime avatars from static/avatars.

Assigns each demo user (marker openids `local-demo-*` / `dev-lead-workbench-*`)
a stable avatar path based on user id so every member gets a different image.
Idempotent: only fills NULL/empty avatars and never overwrites real ones.
Development/testing only.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

AVATAR_COUNT = 82
AVATAR_TEMPLATE = "/static/avatars/matchmaking-anime-{n:03d}.jpg"


def seed_demo_avatars() -> dict[str, int]:
    env = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if env not in {"", "development", "testing"}:
        raise RuntimeError("演示头像回填只允许在 development/testing 环境执行")

    import pymysql
    from database_setup_marriage import get_db_config

    connection = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    cur = connection.cursor()
    cur.execute(
        """SELECT id, gender FROM users
        WHERE openid LIKE 'local-demo-%' OR openid LIKE 'dev-lead-workbench-%'
        ORDER BY id"""
    )
    users = cur.fetchall()
    filled = 0
    # 同性别内不重复：男从 1 号顺取，女从 41 号顺取（82 张对半）。
    male_seq = 0
    female_seq = 0
    for row in users:
        avatar_path = cur.execute("SELECT avatar FROM users WHERE id=%s", (row["id"],)) or None
        cur.execute("SELECT avatar FROM users WHERE id=%s", (row["id"],))
        current = cur.fetchone()
        if current and current["avatar"]:
            continue
        if int(row["gender"] or 0) == 1:
            n = (male_seq % 41) + 1
            male_seq += 1
        else:
            n = (female_seq % 41) + 41
            female_seq += 1
        cur.execute(
            "UPDATE users SET avatar=%s, updated_at=UTC_TIMESTAMP() WHERE id=%s",
            (AVATAR_TEMPLATE.format(n=n), row["id"]),
        )
        filled += 1
    connection.commit()
    connection.close()
    return {"users": len(users), "filled": filled}


if __name__ == "__main__":
    print("seeded:", seed_demo_avatars())
