"""乱码文案防再犯卫兵。

历史事故：源码中的中文字符串因编码损坏退化为连续问号（七个及以上问号串），
被当作用户可见的 HTTP detail 或站内通知文案返回。本卫兵遍历 ``app/**/*.py``
源码，断言任何引号字符串字面量中不出现连续两个及以上的问号。

净指标口径：``app`` 与 ``docs/api`` 双双 0 命中。``docs/`` 下另有历史清单/
计划文档命中（非契约），不在本卫兵范围内。如未来出现合法场景（例如正则
示例、协议文档），必须在本文件 ``_ALLOWLIST`` 中显式登记「文件 + 行号定位
子串」并注明理由，不得静默豁免。
"""

from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

# 引号字符串字面量中的连续问号（双引号与单引号各一条模式）。
_MOJIBAKE_PATTERNS = (
    re.compile(r'"[^"\n]*\?{2,}[^"\n]*"'),
    re.compile(r"'[^'\n]*\?{2,}[^'\n]*'"),
)

# 显式豁免登记表：{相对路径: {定位子串: 理由}}；保持为空即全量零容忍。
_ALLOWLIST: dict[str, dict[str, str]] = {}


def _iter_python_sources() -> list[Path]:
    return sorted(APP_ROOT.rglob("*.py"))


def _is_allowlisted(relative: str, line: str) -> str | None:
    entry = _ALLOWLIST.get(relative)
    if not entry:
        return None
    for marker, reason in entry.items():
        if marker in line:
            return reason
    return None


def test_app_sources_contain_no_mojibake_string_literals() -> None:
    sources = _iter_python_sources()
    assert sources, "app 源码树为空，卫兵配置错误"

    violations: list[str] = []
    for source in sources:
        relative = source.relative_to(APP_ROOT.parent).as_posix()
        for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
            if not any(pattern.search(line) for pattern in _MOJIBAKE_PATTERNS):
                continue
            reason = _is_allowlisted(relative, line)
            if reason:
                continue
            violations.append(f"{relative}:{lineno}: {line.strip()}")

    assert not violations, (
        "发现疑似乱码字符串字面量（连续问号）；请恢复文案，或在 "
        "tests/test_mojibake_guard.py 的 _ALLOWLIST 显式登记豁免：\n" + "\n".join(violations)
    )


def test_allowlist_entries_still_exist() -> None:
    """豁免登记不得指向已不存在的代码，防止 allowlist 腐化。"""
    for relative, markers in _ALLOWLIST.items():
        source = (APP_ROOT.parent / relative).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in source, f"豁免条目 {relative} 的定位子串已不存在: {marker}"
