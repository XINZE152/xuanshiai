"""Q51 超时抓包对账工具（2026-11-11）。

用途：把「客户端看到 request:fail timeout」和「后端日志里到底有没有这次请求」对齐。
做法是**客户端自己注入 `X-Request-ID`**——后端 `app/main.py` 的日志中间件会原样接受
（只要匹配 REQUEST_ID_PATTERN 并回写在响应头），日志格式里每条都带
`request_id=%(request_id)s`。所以同一个 id 既能查到客户端耗时，也能 grep 到后端那一行。

四种结论互斥，拿到一次真实快照就能定位：

======================  ====================================================
verdict                 含义 / 下一步
======================  ====================================================
``not_deployed``        HTTP 404/405：这台环境跑的还是旧代码，先部署。
``proxy_timeout``       HTTP 502/504 且**响应头没有回显 X-Request-ID**：网关先掐了，
                        后端日志里不会有这次请求，要调 nginx `proxy_read_timeout`。
``backend_5xx``         有回显 id 的 5xx：后端自己炸了，直接 grep 这个 id 看堆栈。
``client_gave_up``      客户端超时、服务端其实还在跑：根因是客户端超时短于模型耗时，
                        抬 `AI_LLM_TIMEOUT`，别去反代上找。
``ok``                  2xx：这条路径没问题。
``auth``                401：路由可达，只是缺 token（先 --login-phone）。
======================  ====================================================

用法：

    # 只测公开端点，不要 token
    python scripts/probe_request_trace.py

    # 用测试服账号拿 token 再打 AI 端点
    python scripts/probe_request_trace.py \\
        --login-phone 13100000000 --login-password password123 \\
        --probe "GET /ai/capabilities" \\
        --probe "POST /ai/thoughtfulness" \\
        --repeat 3

注意：AI 端点会真的产生调用费用，`--repeat` 别开大。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

API_PREFIX = "/api/v1"
DEFAULT_TIMEOUT_S = 60.0
#: 客户端（小程序 request）的默认超时，来自 api/config.uts 的 API_CONFIG.timeout。
DEFAULT_CLIENT_TIMEOUT_MS = 15000

_VERDICT_HINT = {
    "ok": "2xx，这条路径没问题",
    "auth": "401：路由可达，只是缺 token（加 --login-phone）",
    "not_deployed": "404/405：这台环境是旧代码，先部署",
    "proxy_timeout": "502/504 且无 X-Request-ID 回显：网关先掐，后端没收到这次请求",
    "backend_5xx": "5xx 但回显了 id：后端自己炸了，grep 该 id 看堆栈",
    "client_gave_up": "客户端先超时，服务端仍在跑：抬客户端超时，不是反代问题",
    "other": "其它状态码，人工判读",
}


@dataclass
class Probe:
    method: str
    path: str
    body: dict | None = None


@dataclass
class Trace:
    name: str
    method: str
    url: str
    request_id: str
    status: int | None
    elapsed_ms: int
    echoed_id: str
    outcome: str
    verdict: str
    body_excerpt: str


def _normalize_path(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if path.startswith(API_PREFIX):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return API_PREFIX + path


def _build_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + _normalize_path(path)


def _parse_probe(raw: str) -> Probe:
    """``"POST /ai/thoughtfulness {\"scene\":\"x\"}"`` → Probe。"""
    parts = raw.strip().split(None, 1)
    method = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ""
    if not rest:
        raise SystemExit(f"probe 缺少路径：{raw}")
    path, _, tail = rest.partition(" ")
    body = None
    tail = tail.strip()
    if tail:
        try:
            body = json.loads(tail)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"probe 的 JSON 体解析失败（{exc}）：{tail}") from exc
    return Probe(method=method, path=path, body=body)


def _classify(
    *,
    status: int | None,
    echoed_id: str,
    outcome: str,
    elapsed_ms: int,
    client_timeout_ms: int,
) -> str:
    if outcome == "client_timeout":
        return "client_gave_up"
    if status is None:
        return "other"
    if status in (502, 504):
        return "proxy_timeout" if not echoed_id else "backend_5xx"
    if status >= 500:
        return "backend_5xx"
    if status in (404, 405):
        return "not_deployed"
    if status == 401:
        return "auth"
    if 200 <= status < 300:
        # 2xx 但已经超过客户端超时：用户体验上仍然是 timeout。
        if elapsed_ms > client_timeout_ms:
            return "client_gave_up"
        return "ok"
    return "other"


def _run_probe(
    client: httpx.Client,
    probe: Probe,
    *,
    base_url: str,
    token: str,
    tag: str,
    index: int,
    timeout_s: float,
    client_timeout_ms: int,
) -> Trace:
    url = _build_url(base_url, probe.path)
    request_id = f"probe-{tag}-{index:02d}"
    headers = {"X-Request-ID": request_id, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    started = time.perf_counter()
    status: int | None = None
    echoed_id = ""
    outcome = "ok"
    excerpt = ""
    try:
        response = client.request(
            probe.method,
            url,
            headers=headers,
            json=probe.body,
            timeout=timeout_s,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        status = response.status_code
        echoed_id = response.headers.get("X-Request-ID", "")
        excerpt = response.text.strip().replace("\n", " ")[:160]
    except httpx.TimeoutException:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        outcome = "client_timeout"
    except httpx.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        outcome = "transport_error"
        excerpt = f"{type(exc).__name__}: {exc}"

    return Trace(
        name=f"{probe.method} {probe.path}",
        method=probe.method,
        url=url,
        request_id=request_id,
        status=status,
        elapsed_ms=elapsed_ms,
        echoed_id=echoed_id,
        outcome=outcome,
        verdict=_classify(
            status=status,
            echoed_id=echoed_id,
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            client_timeout_ms=client_timeout_ms,
        ),
        body_excerpt=excerpt,
    )


def _login(client: httpx.Client, base_url: str, phone: str, password: str) -> str:
    url = _build_url(base_url, "/auth/test-login")
    response = client.post(
        url,
        json={"phone": phone, "password": password},
        headers={"X-Request-ID": "probe-login-00"},
        timeout=DEFAULT_TIMEOUT_S,
    )
    if response.status_code >= 400:
        raise SystemExit(f"登录失败 HTTP {response.status_code}: {response.text[:200]}")
    payload = response.json()
    token = payload.get("access_token") or (payload.get("data") or {}).get("access_token")
    if not token:
        raise SystemExit(f"登录响应里没有 access_token：{payload}")
    return str(token)


def _render(traces: list[Trace], client_timeout_ms: int) -> None:
    print("| 请求 | 状态 | 耗时(ms) | 回显 X-Request-ID | 判定 |")
    print("| --- | --- | --- | --- | --- |")
    for trace in traces:
        status = "-" if trace.status is None else str(trace.status)
        echo = "是" if trace.echoed_id else "**否**"
        print(
            f"| `{trace.method} {trace.name.split(' ', 1)[1]}` | {status} | "
            f"{trace.elapsed_ms} | {echo} | `{trace.verdict}` |"
        )

    print()
    print(f"客户端超时基线：{client_timeout_ms} ms（超出即用户侧感知为 timeout）")
    verdicts = sorted({trace.verdict for trace in traces})
    for verdict in verdicts:
        print(f"- `{verdict}`：{_VERDICT_HINT[verdict]}")

    slow = [trace for trace in traces if trace.status is not None and trace.status < 400]
    if slow:
        worst = max(slow, key=lambda trace: trace.elapsed_ms)
        print()
        print(f"最慢的成功请求：{worst.name} → {worst.elapsed_ms} ms")

    print()
    print("对账用的请求 ID（拿这个去后端 grep `request_id=`，能查到 duration_ms / status）：")
    for trace in traces:
        print(f"  {trace.request_id}  {trace.method} {trace.url}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Q51 超时抓包对账")
    parser.add_argument("--base-url", default="https://xhztest.xyz", help="后端地址，默认测试服")
    parser.add_argument("--probe", action="append", default=[], metavar="METHOD PATH [JSON]")
    parser.add_argument("--repeat", type=int, default=1, help="每个探针重复次数")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="单次请求超时（秒）"
    )
    parser.add_argument(
        "--client-timeout-ms",
        type=int,
        default=DEFAULT_CLIENT_TIMEOUT_MS,
        help="端侧超时基线，用于判断「已经慢到用户会看到 timeout」",
    )
    parser.add_argument("--login-phone", default="", help="用 /auth/test-login 换 token")
    parser.add_argument("--login-password", default="", help="配合 --login-phone")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON，便于存档")
    args = parser.parse_args()

    probes = [_parse_probe(raw) for raw in args.probe] or [
        Probe(method="GET", path="/health"),
        Probe(method="GET", path="/ai/capabilities"),
        Probe(method="GET", path="/support/help-docs?page=1&page_size=20"),
    ]

    tag = uuid.uuid4().hex[:6]
    traces: list[Trace] = []
    with httpx.Client(follow_redirects=False) as client:
        token = ""
        if args.login_phone:
            token = _login(client, args.base_url, args.login_phone, args.login_password)
            print(f"# 已登录 {args.login_phone}，token 长度 {len(token)}\n")

        for index, probe in enumerate(probes):
            for attempt in range(max(1, args.repeat)):
                trace = _run_probe(
                    client,
                    probe,
                    base_url=args.base_url,
                    token=token,
                    tag=tag,
                    index=index * 100 + attempt,
                    timeout_s=args.timeout,
                    client_timeout_ms=args.client_timeout_ms,
                )
                traces.append(trace)
                print(
                    f"[{trace.request_id}] {trace.status or trace.outcome} "
                    f"{trace.elapsed_ms}ms verdict={trace.verdict}",
                    file=sys.stderr,
                )

    print()
    print(f"# 抓包结果（{args.base_url}）")
    print()
    _render(traces, args.client_timeout_ms)

    if args.json:
        print()
        print("```json")
        print(json.dumps([trace.__dict__ for trace in traces], ensure_ascii=False, indent=2))
        print("```")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
