"""artifacts 落盘 + 7 天清理。

每个 run 写到 ``artifacts/moxiang-prompt-loop/<run-id>/`` 下::

    meta.json
    transcript.jsonl
    db-snapshot.sql
    db-snapshot.json
    score.json
    diff.json
    frontend-trace.json (前端直接消费)
    summary.md
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ARTIFACTS_ROOT = Path("artifacts/moxiang-prompt-loop")


@dataclass
class ArtifactPaths:
    root: Path
    meta: Path
    transcript: Path
    db_sql: Path
    db_json: Path
    score: Path
    diff: Path
    frontend_trace: Path
    summary: Path

    @classmethod
    def for_run(cls, run_id: str, root: Path | None = None) -> "ArtifactPaths":
        base = (root or ARTIFACTS_ROOT) / run_id
        return cls(
            root=base,
            meta=base / "meta.json",
            transcript=base / "transcript.jsonl",
            db_sql=base / "db-snapshot.sql",
            db_json=base / "db-snapshot.json",
            score=base / "score.json",
            diff=base / "diff.json",
            frontend_trace=base / "frontend-trace.json",
            summary=base / "summary.md",
        )


def make_run_id(prefix: str = "") -> str:
    stamp = datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
    return f"{prefix}{stamp}" if prefix else stamp


def write_meta(
    paths: ArtifactPaths,
    *,
    transcript_name: str,
    provider: str,
    versions: dict[str, str],
    account_phone: str,
    user_id: int,
    extra: dict[str, Any] | None = None,
) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    payload = {
        "transcript": transcript_name,
        "provider": provider,
        "prompt_versions": versions,
        "account_phone": account_phone,
        "user_id": user_id,
        "captured_at": datetime.utcnow().isoformat() + "Z",
    }
    if extra:
        payload.update(extra)
    paths.meta.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_score(paths: ArtifactPaths, score_bundle: Any) -> None:
    payload = score_bundle.to_dict() if hasattr(score_bundle, "to_dict") else score_bundle
    paths.score.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_diff(paths: ArtifactPaths, baseline_score: dict[str, Any] | None, current_score: dict[str, Any]) -> None:
    diff: dict[str, Any] = {
        "has_baseline": baseline_score is not None,
        "delta_per_dimension": {},
        "total_delta": None,
        "regressions": [],
    }
    if baseline_score is None:
        paths.diff.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    base_reports = {r["name"]: r["score"] for r in baseline_score.get("reports", [])}
    for report in current_score.get("reports", []):
        name = report["name"]
        prev = base_reports.get(name, 0.0)
        delta = round(report["score"] - prev, 2)
        diff["delta_per_dimension"][name] = delta
        if delta <= -10.0:
            diff["regressions"].append({"name": name, "delta": delta})
    diff["total_delta"] = round(current_score["total"] - baseline_score.get("total", 0.0), 2)
    paths.diff.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(paths: ArtifactPaths, score_bundle: Any, meta: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append(f"# 墨相师·提示词闭环 · {meta.get('transcript', '?')}")
    lines.append("")
    lines.append(f"- run_id: `{paths.root.name}`")
    lines.append(f"- provider: `{meta.get('provider')}`")
    lines.append(f"- prompt_versions: `{meta.get('prompt_versions')}`")
    lines.append(f"- captured_at: `{meta.get('captured_at')}`")
    lines.append("")
    score = score_bundle.to_dict() if hasattr(score_bundle, "to_dict") else score_bundle
    lines.append(f"## 总分: **{score['total']}** (all_passed={score['all_passed']})")
    lines.append("")
    for report in score["reports"]:
        lines.append(f"### {report['name']} — {report['score']} (threshold {report['threshold']})")
        lines.append(f"- passed: **{report['passed']}**")
        lines.append(f"- details: `{json.dumps(report.get('details', {}), ensure_ascii=False)[:400]}`")
        lines.append("")
    paths.summary.write_text("\n".join(lines), encoding="utf-8")


def cleanup_old_runs(keep_days: int = 7, root: Path | None = None) -> int:
    """清理 ``keep_days`` 天前的 run 目录。返回删除数量。"""
    base = root or ARTIFACTS_ROOT
    if not base.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    deleted = 0
    for entry in base.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except FileNotFoundError:
            continue
        if mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            deleted += 1
    return deleted


def find_latest_baseline(
    transcript_name: str,
    root: Path | None = None,
) -> Path | None:
    """找最近一次同 transcript 的 baseline.json(若有)。"""
    base = root or ARTIFACTS_ROOT
    if not base.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for entry in base.iterdir():
        baseline = entry / "baseline.json"
        if baseline.exists():
            try:
                meta = json.loads((entry / "meta.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if meta.get("transcript") == transcript_name:
                candidates.append((entry.stat().st_mtime, entry))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1] / "score.json"
