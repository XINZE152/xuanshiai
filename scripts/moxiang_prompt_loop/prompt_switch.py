"""提示词版本热切 — 仅 in-process 修改 Python 常量,跨进程不生效。

脚本必须独占一个 FastAPI 实例:uvicorn 启动的服务进程或 in-process
测试 client。本模块在脚本入口 / 退出时分别 ``apply_versions`` 与
``restore_versions``,避免污染后续测试。
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import Iterator

# 三份提示词的版本常量在以下模块里
_TARGETS = (
    "app.services.ai.prompts.moxiang_master",
    "app.services.ai.prompts.profile_extract",
    "app.services.ai.prompts.profile_narrative",
    "app.services.ai.profile",
)


def _snapshot() -> dict[str, object]:
    out: dict[str, object] = {}
    for path in _TARGETS:
        mod = importlib.import_module(path)
        for attr in (
            "MOXIANG_MASTER_PROMPT_VERSION",
            "PROFILE_PROMPT_VERSION",
            "NARRATIVE_PROMPT_VERSION",
            "PROMPT_VERSION",
        ):
            if hasattr(mod, attr):
                out[f"{path}:{attr}"] = getattr(mod, attr)
    return out


def _apply(versions: dict[str, str]) -> None:
    """根据 ``{"master": "v1.1", "extract": "v1.1", "narrative": "v4"}`` 应用。"""
    for key, value in versions.items():
        if key == "master":
            mod = importlib.import_module("app.services.ai.prompts.moxiang_master")
            mod.MOXIANG_MASTER_PROMPT_VERSION = value
        elif key == "extract":
            mod = importlib.import_module("app.services.ai.prompts.profile_extract")
            mod.PROMPT_VERSION = value
            mod2 = importlib.import_module("app.services.ai.profile")
            mod2.PROFILE_PROMPT_VERSION = value
        elif key == "narrative":
            mod = importlib.import_module("app.services.ai.prompts.profile_narrative")
            mod.PROMPT_VERSION = value
            mod2 = importlib.import_module("app.services.ai.profile")
            mod2.NARRATIVE_PROMPT_VERSION = value
        else:
            raise ValueError(f"unknown prompt version key: {key}")


def _restore(snapshot: dict[str, object]) -> None:
    for ref, original in snapshot.items():
        path, attr = ref.rsplit(":", 1)
        mod = importlib.import_module(path)
        setattr(mod, attr, original)


@contextmanager
def patched(versions: dict[str, str]) -> Iterator[None]:
    """在 with 块内临时替换提示词版本,退出时还原。"""
    snap = _snapshot()
    try:
        _apply(versions)
        yield
    finally:
        _restore(snap)


def current_versions() -> dict[str, str]:
    """读取当前进程内的三个版本常量。"""
    out: dict[str, str] = {}
    for path in _TARGETS:
        mod = importlib.import_module(path)
        for attr, key in (
            ("MOXIANG_MASTER_PROMPT_VERSION", "master"),
            ("PROFILE_PROMPT_VERSION", "extract"),
            ("NARRATIVE_PROMPT_VERSION", "narrative"),
        ):
            if hasattr(mod, attr):
                out[key] = getattr(mod, attr)
                break
    return out
