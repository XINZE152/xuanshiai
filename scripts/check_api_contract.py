#!/usr/bin/env python
"""前后端接口对账（只读）。

为什么需要它
    2026-11-23 安全中心页上线后全站报「数据加载失败」：前端 ``api/security.uts`` 调
    ``GET /security/center``，后端从未实现该路由 → 生产 404，而前后端各自的测试都绿。
    这类「前端已上、后端没有」的缺口靠人工发现太晚，本脚本把它变成一条可执行的闸门。

本脚本做什么
    1. 从后端 OpenAPI（``app.main.app``）取全部路径，去掉 ``/api/v1`` 前缀；
    2. 从 ``<frontend>/api/*.uts`` 里抽取 ``url: '...'`` 字面量；
    3. 逐条匹配（精确 / 动态拼接前缀 / ``{param}`` 模板）；
    4. 与 ``scripts/api_contract_baseline.json`` 比对：
       - 基线里没有的新失配 → 失败（退出码 1），说明前端调了一个后端不存在的接口；
       - 基线里已记录、现在能匹配上的 → 提示可以删除该条，不影响退出码。

用法
    python scripts/check_api_contract.py
    python scripts/check_api_contract.py --frontend ../xuanshiai-vue
    python scripts/check_api_contract.py --json
    python scripts/check_api_contract.py --update-baseline   # 记录当前缺口（需评审后提交）

退出码
    0 = 与基线一致；1 = 出现新的失配；2 = 取不到 OpenAPI 或前端目录
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASELINE_PATH = Path(__file__).resolve().with_name("api_contract_baseline.json")

#: url: '/x/y'  —— 只认以 / 开头的站内路径，跳过 http(s) 绝对地址。
_LITERAL_RE = re.compile(r"url:\s*(['\"])(/[^'\"]*)\1")
#: 页面路由与小程序 API 不是后端接口，直接跳过。
_IGNORED_PREFIXES = ("/pages/", "/pagesSub/", "/static/")


def normalize_path(path: str) -> str:
    """去掉查询串与末尾斜杠，保留 / 开头。"""
    clean = path.split("?")[0].strip()
    if len(clean) > 1 and clean.endswith("/"):
        clean = clean[:-1]
    return clean


def openapi_path_set(raw_paths: Iterable[str]) -> set[str]:
    """OpenAPI 路径去掉 /api/v1 前缀，方便与前端字面量对齐。"""
    result: set[str] = set()
    for path in raw_paths:
        stripped = path[len("/api/v1") :] if path.startswith("/api/v1") else path
        result.add(normalize_path(stripped))
    return result


def extract_urls(text: str) -> list[str]:
    """抽取一个 .uts 文件里的接口字面量（去重、跳过页面路由）。"""
    found: list[str] = []
    seen: set[str] = set()
    for match in _LITERAL_RE.finditer(text):
        literal = normalize_path(match.group(2))
        if literal == "" or literal in seen:
            continue
        if literal.startswith(_IGNORED_PREFIXES):
            continue
        seen.add(literal)
        found.append(literal)
    return found


def compile_templates(path_set: Iterable[str]) -> list[re.Pattern[str]]:
    """把后端 ``/x/{id}/y`` 路径编译成「任意一段」的正则，供字面量反查。"""
    compiled: list[re.Pattern[str]] = []
    for path in path_set:
        if "{" not in path:
            continue
        parts = re.split(r"(\{[^}]+\})", path)
        pattern = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
        compiled.append(re.compile(pattern))
    return compiled


def matches(literal: str, path_set: set[str], templates: list[re.Pattern[str]] | None = None) -> bool:
    """字面量能否对上某个后端路径。

    三种形态：
    1. 精确相等；
    2. 动态拼接（``'/x/' + id``）→ 后端有以该前缀开头的路径；
    3. 后端是 ``/x/{id}`` 模板、前端写死了某个 id。
    """
    if literal in path_set:
        return True
    if any(path.startswith(literal + "/") for path in path_set):
        return True
    if templates is None:
        templates = compile_templates(path_set)
    return any(template.fullmatch(literal) is not None for template in templates)


def load_baseline(path: Path) -> dict[tuple[str, str], str]:
    """基线条目：(文件名, 字面量) -> 原因。"""
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") or []
    return {(str(item["file"]), str(item["url"])): str(item.get("reason", "")) for item in entries}


def scan(frontend: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """扫描前端 api 目录，返回 (命中, 失配)，元素为 (文件名, 字面量)。"""
    api_dir = frontend / "api"
    if not api_dir.is_dir():
        raise FileNotFoundError(f"找不到前端 api 目录：{api_dir}")
    from app.main import app  # 延迟导入：只在真正扫描时拉起 FastAPI 应用

    path_set = openapi_path_set(app.openapi().get("paths", {}).keys())
    templates = compile_templates(path_set)
    matched: list[tuple[str, str]] = []
    unmatched: list[tuple[str, str]] = []
    for file in sorted(api_dir.glob("*.uts")):
        for literal in extract_urls(file.read_text(encoding="utf-8")):
            bucket = matched if matches(literal, path_set, templates) else unmatched
            bucket.append((file.name, literal))
    return matched, unmatched


def write_baseline(unmatched: list[tuple[str, str]], path: Path) -> None:
    payload: dict[str, Any] = {
        "note": "前端调用但后端 OpenAPI 里没有的接口。新增失配必须评审后再登记；能匹配上的条目请删除。",
        "entries": [
            {"file": file, "url": url, "reason": "待评估"}
            for file, url in sorted(set(unmatched))
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="比对前端 api/*.uts 与后端 OpenAPI，发现缺失路由")
    parser.add_argument(
        "--frontend",
        default=os.environ.get("XUANSHIAI_VUE_DIR", str(ROOT.parent / "xuanshiai-vue")),
        help="前端仓库根目录（也可用环境变量 XUANSHIAI_VUE_DIR；默认 ../xuanshiai-vue）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于流水线消费")
    parser.add_argument("--update-baseline", action="store_true", help="把当前失配写入基线文件")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        matched, unmatched = scan(Path(args.frontend))
    except Exception as exc:  # noqa: BLE001 - 取不到 OpenAPI/目录都算配置问题
        print(f"check_api_contract: {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        write_baseline(unmatched, BASELINE_PATH)
        print(f"baseline 已写入 {BASELINE_PATH}（{len(set(unmatched))} 条，请补原因后提交）")
        return 0

    baseline = load_baseline(BASELINE_PATH)
    current = set(unmatched)
    new_gaps = sorted(current - set(baseline))
    resolved = sorted(set(baseline) - current)

    if args.json:
        print(
            json.dumps(
                {
                    "matched": len(matched),
                    "unmatched": len(current),
                    "new_gaps": [{"file": f, "url": u} for f, u in new_gaps],
                    "resolved": [{"file": f, "url": u} for f, u in resolved],
                    "baseline": len(baseline),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"接口对账：命中 {len(matched)} 条，失配 {len(current)} 条"
            f"（基线 {len(baseline)} 条，新增 {len(new_gaps)} 条）"
        )
        for file, url in new_gaps:
            print(f"  [新增失配] {file}: {url}  ← 前端调用了后端不存在的接口")
        for file, url in resolved:
            print(f"  [可删基线] {file}: {url}  ← 已能匹配，删掉这条记录")

    if new_gaps:
        if not args.json:
            print("结论：发现新的前后端接口缺口（退出码 1）")
        return 1
    if not args.json:
        print("结论：与基线一致（基线内条目属已知历史路径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
