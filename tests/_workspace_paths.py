"""跨仓文档与素材的路径解析 —— 测试侧的唯一实现。

``PRODUCT.md`` 与架构契约是前后端共享的定版文档，实际维护在前端仓库
（``xuanshiai-vue/PRODUCT.md`` + ``xuanshiai-vue/docs/architecture/``）。后端单独
checkout 时，两个仓库并不保证并列在同一父目录——本机就是
``E:\\HTML\\xuanshiai-vue`` 与 ``E:\\houduan\\xuanshiai``——因此任何基于相对层级的
写死路径（例如 ``Path(__file__).parents[2]``）都只对某台机器成立：换机器或 CI 时它
会指向一个与文档无关的目录，测试只能报「文档缺失」，而真实原因是找错了地方。

解析顺序（逐级兜底，命中即返回）：

1. 进程环境变量 ``XUANSHIAI_FRONTEND_ROOT``（CI / 部署显式指路，最高优先级）；
2. 后端仓库根 ``.env`` 里的同名键。该文件已被 gitignore，是本项目既有的本地配置面；
   pytest 不会自动加载它，所以这里显式读一行，避免「每条命令都要先 export」；
3. 从后端根逐级向上查找含 ``PRODUCT.md`` 的目录（兼容 monorepo / worktree 嵌套）；
4. 同级 ``xuanshiai-vue``（两仓并列的常规本地布局）。

注意：这里的第 4 步只覆盖「并列」这一种布局，不试图扫描整个磁盘去找仓库。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["FRONTEND_ENV", "candidate_roots", "find_frontend_root", "require_frontend_root"]

FRONTEND_ENV = "XUANSHIAI_FRONTEND_ROOT"

# 向上查找的最大层数：够覆盖 worktree / 子目录布局，又不至于一路爬到盘符根。
_UPWARD_DEPTH = 5

_HINT = f"（文档在前端仓库；可用环境变量或在后端 .env 中设置 {FRONTEND_ENV}=<前端仓库根>）"


def _dotenv_value(backend_root: Path, key: str) -> str | None:
    """从后端根 ``.env`` 读一个键，缺失或格式异常时返回 None。

    只做最小的 ``KEY=VALUE`` 解析：不处理多行值、变量插值或引号转义——这些超出
    本函数需要承载的范围，交给真正的配置加载器。
    """
    env_file = backend_root / ".env"
    if not env_file.is_file():
        return None
    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            value = value.strip().strip('"').strip("'")
            return value or None
    return None


def candidate_roots(backend_root: Path) -> list[Path]:
    """按优先级返回候选的前端仓库根（可能含重复项，由调用方去重）。"""
    candidates: list[Path] = []

    override = os.environ.get(FRONTEND_ENV)
    if override:
        candidates.append(Path(override))

    from_dotenv = _dotenv_value(backend_root, FRONTEND_ENV)
    if from_dotenv:
        candidates.append(Path(from_dotenv))

    current = backend_root
    for _ in range(_UPWARD_DEPTH):
        candidates.append(current)
        current = current.parent

    candidates.append(backend_root.parent / "xuanshiai-vue")
    return candidates


def find_frontend_root(backend_root: Path | None = None) -> Path | None:
    """返回第一个真正含 ``PRODUCT.md`` 的候选目录；都不可用时返回 None。

    刻意不抛异常：让调用方决定是跳过还是失败，并带上自己的上下文说明。
    """
    root = backend_root or Path(__file__).resolve().parents[1]
    for candidate in candidate_roots(root):
        if (candidate / "PRODUCT.md").is_file():
            return candidate
    return None


def require_frontend_root(backend_root: Path | None = None) -> Path:
    """同 :func:`find_frontend_root`，但找不到时抛带指路提示的异常。"""
    found = find_frontend_root(backend_root)
    if found is None:
        raise FileNotFoundError(f"未找到含 PRODUCT.md 的前端仓库根{_HINT}")
    return found
