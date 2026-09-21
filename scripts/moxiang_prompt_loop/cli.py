"""墨相师·提示词闭环 CLI 入口。

::

    python -m scripts.moxiang_prompt_loop.cli \\
        --transcript tests/fixtures/moxiang/transcripts/30turn-baseline.jsonl \\
        --provider dots \\
        --prompt-versions master=v1.1,extract=v1.1,narrative=v4 \\
        --account e2e-moxiang-test/account1.json \\
        --out artifacts/moxiang-prompt-loop/2026-09-04T1030Z \\
        --frontend-trace xuanshiai-vue/static/dev/moxiang-trace.json \\
        --reset-session \\
        --set-baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from scripts.moxiang_prompt_loop import (
    artifacts as art,
    db_snapshot,
    frontend_trace,
    prompt_switch,
    transcript,
    ws_client,
)
from scripts.moxiang_prompt_loop.scorers import run_all as run_scorers


def _parse_versions(raw: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw:
        return out
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k and v:
            out[k] = v
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="墨相师·提示词闭环")
    parser.add_argument("--transcript", required=True, help="固定对话 fixtures 路径")
    parser.add_argument("--provider", default="dots", help="AI provider 名称")
    parser.add_argument(
        "--prompt-versions",
        default="",
        help="形如 master=v1.1,extract=v1.1,narrative=v4",
    )
    parser.add_argument(
        "--account",
        default="e2e-moxiang-test/account1.json",
        help="测试账号 JSON 路径",
    )
    parser.add_argument(
        "--out",
        default="",
        help="artifacts 输出目录(run-id 或绝对路径)",
    )
    parser.add_argument(
        "--frontend-trace",
        default="",
        help="前端 dev trace JSON 输出路径",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="后端入口地址",
    )
    parser.add_argument("--reset-session", action="store_true", help="回放前归档 active 会话")
    parser.add_argument("--set-baseline", action="store_true", help="把本次 score.json 落为 baseline")
    parser.add_argument("--keep-days", type=int, default=7, help="artifacts 保留天数")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    # 1. 加载 transcript
    t = transcript.load(args.transcript)
    print(f"[loop] transcript={t.name} turns={len([x for x in t.turns if x.get('role') == 'user'])}")

    # 2. 加载账号
    account = json.loads(Path(args.account).read_text(encoding="utf-8"))
    phone = str(account.get("phone", ""))
    if not phone:
        print("[loop] account missing phone", file=sys.stderr)
        return 3

    # 3. 应用提示词版本
    versions = _parse_versions(args.prompt_versions)
    versions_to_apply = versions or prompt_switch.current_versions()
    with prompt_switch.patched(versions_to_apply):
        # 4. 跑 WS 回放
        client = ws_client.MoxiangWSClient(
            base_url=args.base_url,
            phone=phone,
            pre_issued_token=str(account.get("token") or ""),
            pre_issued_user_id=int(account.get("user_id") or 0),
            refresh_token=str(account.get("refresh_token") or ""),
            password=str(account.get("password") or ""),
        )
        result = await client.replay(
            t,
            reset_session=args.reset_session,
        )
        print(
            f"[loop] replayed turns={len(result.turns)} elapsed={result.elapsed_seconds}s "
            f"session_id={result.session_id}"
        )

        # 5. DB 快照
        snapshot = db_snapshot.Snapshot.run(
            user_id=int(account.get("user_id", 0)),
            session_id=result.session_id,
        )
        print(
            f"[loop] db_snapshot stats={snapshot.stats}"
        )

        # 6. 评分
        run_meta: dict[str, Any] = {
            "transcript_name": t.name,
            "expect_dimensions": t.expect_dimensions,
            "ai_replies": result.ai_replies(),
        }
        score_bundle = run_scorers(run_meta, snapshot)
        print(f"[loop] total={score_bundle.total} all_passed={score_bundle.all_passed}")
        for r in score_bundle.reports:
            print(f"        - {r.name}: {r.score} (passed={r.passed})")

        # 7. artifacts 落盘
        run_id = (
            args.out
            if args.out and not args.out.endswith("/")
            else art.make_run_id(prefix="run-")
        )
        paths = art.ArtifactPaths.for_run(run_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        art.write_meta(
            paths,
            transcript_name=t.name,
            provider=args.provider,
            versions=versions_to_apply,
            account_phone=phone,
            user_id=int(account.get("user_id", 0)),
            extra={
                "session_id": result.session_id,
                "elapsed_seconds": result.elapsed_seconds,
                "turn_count": len(result.turns),
            },
        )
        snapshot.to_json(paths.db_json)
        snapshot.to_sql(paths.db_sql)
        ws_client.write_transcript_jsonl(result, paths.transcript)
        art.write_score(paths, score_bundle)

        baseline_score_path = art.find_latest_baseline(t.name)
        baseline_score: dict[str, Any] | None = None
        if baseline_score_path is not None:
            baseline_score = json.loads(
                baseline_score_path.read_text(encoding="utf-8")
            )
        art.write_diff(paths, baseline_score, score_bundle.to_dict())
        art.write_summary(
            paths,
            score_bundle,
            json.loads(paths.meta.read_text(encoding="utf-8")),
        )

        if args.set_baseline:
            (paths.root / "baseline.json").write_text(
                json.dumps(score_bundle.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # 8. 前端 trace 导出(可选)
        if args.frontend_trace:
            frontend_trace.export_trace(
                result,
                score_bundle.total,
                Path(args.frontend_trace),
                score_breakdown={
                    r.name: r.score for r in score_bundle.reports
                },
            )

        # 9. 清理
        deleted = art.cleanup_old_runs(args.keep_days)
        if deleted:
            print(f"[loop] cleanup_old_runs deleted={deleted}")

    # 退出码
    if not score_bundle.all_passed:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except RuntimeError as exc:
        print(f"[loop] runtime_error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
