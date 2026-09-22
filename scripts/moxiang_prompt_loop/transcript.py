"""固定对话 transcript loader。

读取 ``tests/fixtures/moxiang/transcripts/*.jsonl``,每个文件可包含多个
JSON 对象(每行一个 JSON 对象;若只有一条,也可单行 JSONL)。

输出 :class:`Transcript` 含:

- name
- subject
- turns(原始 list)
- expect_dimensions: 给 dimension scorer 用
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Transcript:
    name: str
    subject: str
    description: str
    turns: list[dict[str, object]]
    expect_dimensions: list[dict[str, object]] = field(default_factory=list)

    def user_texts(self) -> list[str]:
        return [str(t.get("text", "")) for t in self.turns if t.get("role") == "user"]


def load(path: Path | str) -> Transcript:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"transcript file not found: {path}")

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"transcript is empty: {path}")

    objects: list[dict[str, object]] = []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) > 1:
        # 尝试每行独立 JSON;任一行解析失败则回退到整文件解析
        all_ok = True
        parsed: list[dict[str, object]] = []
        for line_idx, line in enumerate(lines, start=1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                all_ok = False
                break
            if not isinstance(obj, dict):
                all_ok = False
                break
            parsed.append(obj)
        if all_ok:
            objects = parsed
        else:
            try:
                root = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"transcript parse error at {path}: {exc}"
                ) from exc
            if isinstance(root, dict):
                objects = [root]
            elif isinstance(root, list):
                objects = [o for o in root if isinstance(o, dict)]
            else:
                raise ValueError(f"transcript root must be object or array: {path}")
    else:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"transcript parse error at {path}: {exc}"
            ) from exc
        if isinstance(obj, dict):
            objects = [obj]
        elif isinstance(obj, list):
            objects = [o for o in obj if isinstance(o, dict)]
        else:
            raise ValueError(f"transcript root must be object or array: {path}")

    if not objects:
        raise ValueError(f"transcript has no objects: {path}")

    head = objects[0]
    name = str(head.get("name") or path.stem)
    subject = str(head.get("subject") or "personal")
    description = str(head.get("description") or "")
    turns: list[dict[str, object]] = []
    expect_dimensions: list[dict[str, object]] = []

    # 仅在第一行带 turns 时为单 JSONL;否则多文件拼接
    if "turns" in head:
        turns = list(head.get("turns") or [])
        # 真实 user-turn 编号(1-based,忽略 assistant 行)
        user_idx = 0
        for t in turns:
            if t.get("role") != "user":
                continue
            user_idx += 1
            expect_dimensions.append(
                {
                    "turn": user_idx,
                    "dimensions": t.get("expect_dimensions") or [],
                    "category": t.get("category"),
                }
            )

    return Transcript(
        name=name,
        subject=subject,
        description=description,
        turns=turns,
        expect_dimensions=expect_dimensions,
    )
