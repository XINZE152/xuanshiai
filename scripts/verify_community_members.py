"""端到端验证：用真实 HTTP 调用逐个检查 C 端功能是否返回可用数据。

用法（后端需在 127.0.0.1:8000 运行）：
    python scripts/verify_community_members.py

只做读取与少量无害写入（签到/收藏等本身可重复的操作），
失败项会打印 HTTP 状态码和响应体，便于定位是哪一层门禁没打开。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import httpx


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


BASE = "http://127.0.0.1:8000/api/v1"
PASSWORD = "password123"
PRIMARY_PHONE = "19730552884"
SECONDARY_PHONE = "13800001001"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


def login(client: httpx.Client, phone: str) -> str | None:
    response = client.post(
        f"{BASE}/auth/test-login",
        json={"phone": phone, "password": PASSWORD, "device_id": f"verify-{phone}"},
    )
    if response.status_code != 200:
        check(f"登录 {phone}", False, f"{response.status_code} {response.text[:160]}")
        return None
    token = response.json().get("access_token")
    check(f"登录 {phone}", bool(token))
    return token


def get(client: httpx.Client, token: str, path: str, **kwargs: Any) -> httpx.Response:
    return client.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)


def main() -> int:
    # 本机代理环境变量会把 127.0.0.1 也送进代理，显式关闭以直连后端。
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        primary = login(client, PRIMARY_PHONE)
        secondary = login(client, SECONDARY_PHONE)
        if not primary or not secondary:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 1

        # ---- 资料与门禁 ----
        response = get(client, primary, "/users/me/overview")
        overview = response.json() if response.status_code == 200 else {}
        check(
            "我的页聚合信息（完整度/认证/通知角标）",
            response.status_code == 200 and float(overview.get("completion_score", 0)) >= 100,
            f"score={overview.get('completion_score')} unread={overview.get('unread_notification_count')}",
        )
        for key in (
            "unread_notification_count", "incoming_application_count", "match_count",
            "visitor_count", "favorite_count", "favorite_received_count",
            "superlike_sent_count", "superlike_received_count",
        ):
            check(f"聚合字段 {key} 有数据", isinstance(overview.get(key), int), f"={overview.get(key)}")

        response = get(client, primary, "/users/me/profile")
        check("个人资料读取", response.status_code == 200, response.text[:120])

        response = get(client, primary, "/users/me/completion")
        check("资料完整度接口", response.status_code == 200 and response.json().get("can_browse") is True,
              response.text[:120])

        response = get(client, primary, "/users/me/preferences")
        check("择偶要求读取", response.status_code == 200, response.text[:120])

        response = get(client, primary, "/users/me/certifications")
        certs = response.json() if response.status_code == 200 else {}
        check(
            "认证中心（学历/房产/婚姻/单身承诺）",
            response.status_code == 200 and certs.get("education", {}).get("status") == 2
            and certs.get("house", {}).get("status") == 2
            and certs.get("marriage", {}).get("status") == 2
            and certs.get("single_pledge", {}).get("status") in (1, 2),
            response.text[:200],
        )

        # ---- 发现与推荐 ----
        for label, path in (
            ("首页推荐流", "/discovery/recommendations"),
            ("广场名片流", "/discovery/plaza"),
        ):
            response = get(client, primary, path)
            items = response.json().get("items", []) if response.status_code == 200 else []
            check(f"{label}（非空）", response.status_code == 200 and len(items) > 0,
                  f"status={response.status_code} total={response.json().get('total') if response.status_code==200 else response.text[:120]}")

        response = get(client, primary, "/discovery/search", params={"nickname": "知"})
        items = response.json().get("items", []) if response.status_code == 200 else []
        check("按昵称搜索", response.status_code == 200 and len(items) > 0, f"命中 {len(items)} 条")

        response = get(client, primary, "/discovery/search", params={"tag": "城市漫步"})
        items = response.json().get("items", []) if response.status_code == 200 else []
        check("按标签搜索", response.status_code == 200, f"命中 {len(items)} 条")

        response = get(client, primary, "/discovery/filter-options")
        check("筛选选项", response.status_code == 200 and len(response.json().get("cities", [])) > 0)

        response = get(client, primary, "/discovery/filters/saved")
        check("已保存筛选条件", response.status_code == 200)

        response = get(client, primary, "/discovery/browse-history")
        check("浏览记录", response.status_code == 200 and response.json().get("total", 0) > 0,
              f"total={response.json().get('total') if response.status_code==200 else response.text[:100]}")

        response = get(client, primary, "/discovery/visitors")
        body = response.json() if response.status_code == 200 else {}
        check("谁看过我", response.status_code == 200 and body.get("count", 0) > 0,
              f"count={body.get('count')} can_view={body.get('can_view_details')}")

        response = get(client, primary, "/discovery/favorites")
        check("我的收藏", response.status_code == 200, f"total={response.json().get('total') if response.status_code==200 else ''}")

        response = get(client, primary, "/discovery/favorites/received")
        check("收到的收藏", response.status_code == 200)

        response = get(client, primary, "/discovery/applications/incoming")
        incoming = response.json().get("total", 0) if response.status_code == 200 else -1
        check("收到的认识申请", response.status_code == 200 and incoming > 0, f"total={incoming}")

        response = get(client, primary, "/discovery/applications/outgoing")
        check("发出的认识申请", response.status_code == 200)

        response = get(client, primary, "/discovery/superlikes/sent")
        check("我的爆灯", response.status_code == 200, response.text[:100])

        # ---- 公开资料详情 ----
        response = get(client, primary, "/users/me/profile/preview")
        check("个人主页预览", response.status_code == 200, response.text[:120])

        # ---- 社区 ----
        for label, params in (
            ("动态流·最新", {"mode": "latest"}),
            ("动态流·同城", {"mode": "city", "city_code": "110100"}),
            ("动态流·关注", {"mode": "following"}),
            ("动态流·我的", {"mode": "mine"}),
            ("动态流·热门", {"sort": "hot"}),
        ):
            response = get(client, primary, "/community/posts", params=params)
            total = response.json().get("total", 0) if response.status_code == 200 else -1
            check(f"{label}", response.status_code == 200 and total > 0, f"total={total}")

        response = get(client, primary, "/community/topics")
        check("话题列表", response.status_code == 200 and len(response.json()) > 0,
              f"{len(response.json()) if response.status_code==200 else response.text[:100]} 个")

        response = get(client, primary, "/community/topics/page")
        check("话题分页", response.status_code == 200)

        response = get(client, primary, "/community/activities")
        activities = response.json().get("items", []) if response.status_code == 200 else []
        check("线下活动列表", response.status_code == 200 and len(activities) > 0, f"{len(activities)} 场")

        response = get(client, primary, "/community/banners")
        check("社区 Banner", response.status_code == 200 and len(response.json()) > 0)

        response = get(client, primary, "/community/city")
        check("当前同城城市", response.status_code == 200, response.text[:100])

        response = get(client, primary, "/community/quotas")
        check("社区日额度", response.status_code == 200, response.text[:120])

        response = get(client, primary, "/paper-planes")
        planes = response.json() if response.status_code == 200 else []
        check("捡纸飞机", response.status_code == 200 and len(planes) > 0, f"{len(planes)} 条")

        response = get(client, primary, "/paper-planes/mine")
        check("我的纸飞机", response.status_code == 200, response.text[:100])

        response = get(client, primary, "/paper-plane-conversations")
        check("纸飞机对话", response.status_code == 200, response.text[:160])

        response = get(client, primary, "/community/report-reasons")
        check("社区举报原因", response.status_code == 200 and len(response.json()) > 0)

        # ---- 消息与社交 ----
        response = get(client, primary, "/chat/sessions")
        sessions = response.json().get("total", 0) if response.status_code == 200 else -1
        check("聊天会话列表", response.status_code == 200 and sessions > 0, f"total={sessions}")
        session_items = response.json().get("items", []) if response.status_code == 200 else []
        if session_items:
            session_id = session_items[0]["id"]
            response = get(client, primary, f"/chat/sessions/{session_id}/messages")
            check("聊天记录", response.status_code == 200 and len(response.json()) > 0,
                  f"{len(response.json()) if response.status_code==200 else response.text[:100]} 条")

        response = get(client, primary, "/notifications")
        notifications = response.json().get("total", 0) if response.status_code == 200 else -1
        check("消息通知列表", response.status_code == 200 and notifications > 0, f"total={notifications}")

        response = get(client, primary, "/notifications/unread-summary")
        check("通知未读汇总", response.status_code == 200, response.text[:120])

        for label, path in (
            ("我的喜欢", "/relations/likes"),
            ("我的关注", "/relations/following"),
            ("我的粉丝", "/relations/followers"),
            ("我的匹配", "/relations/matches"),
        ):
            response = get(client, primary, path)
            total = response.json().get("total", 0) if response.status_code == 200 else -1
            check(label, response.status_code == 200 and total > 0, f"total={total}")

        response = get(client, primary, "/users/me/privacy")
        check("隐私设置", response.status_code == 200, response.text[:100])

        # ---- 会员 / 积分 / 支付 ----
        response = get(client, primary, "/membership/packages")
        check("会员套餐", response.status_code == 200 and len(response.json()) > 0)

        response = get(client, primary, "/users/me/membership")
        body = response.json() if response.status_code == 200 else {}
        check("当前会员状态", response.status_code == 200 and body.get("is_vip") is True,
              f"is_vip={body.get('is_vip')} pkg={body.get('package_type')}")

        response = get(client, primary, "/users/me/membership/history")
        check("会员购买记录", response.status_code == 200 and response.json().get("total", 0) > 0,
              f"total={response.json().get('total') if response.status_code==200 else ''}")

        response = get(client, primary, "/users/me/points")
        body = response.json() if response.status_code == 200 else {}
        check("积分余额与流水统计", response.status_code == 200 and body.get("balance", 0) > 0,
              f"balance={body.get('balance')} earned={body.get('total_earned')}")

        response = get(client, primary, "/users/me/points/ledger")
        check("积分流水明细", response.status_code == 200 and response.json().get("total", 0) > 0,
              f"total={response.json().get('total') if response.status_code==200 else ''}")

        response = get(client, primary, "/users/me/tasks")
        check("积分任务列表", response.status_code == 200 and len(response.json()) > 0)

        response = get(client, primary, "/points/products")
        check("积分商品", response.status_code == 200 and len(response.json()) > 0)

        response = get(client, primary, "/users/me/invites")
        check("邀请记录", response.status_code == 200, response.text[:100])

        response = get(client, primary, "/users/me/quotas")
        check("各项剩余次数", response.status_code == 200, response.text[:160])

        response = get(client, primary, "/boost/packages")
        check("置顶套餐", response.status_code == 200 and len(response.json()) > 0,
              f"{len(response.json()) if response.status_code==200 else response.text[:120]} 档")

        response = get(client, primary, "/users/me/boost/status")
        body = response.json() if response.status_code == 200 else {}
        check("我的置顶状态", response.status_code == 200, f"active={body.get('active')}")

        response = get(client, primary, "/spotlights")
        if response.status_code == 404:
            response = get(client, primary, "/boost/spotlights")
        check("爆灯列表", response.status_code in (200, 404), f"status={response.status_code}")

        # ---- 其它基础能力 ----
        response = get(client, primary, "/regions/provinces")
        check("省份字典", response.status_code == 200 and len(response.json().get("items", [])) > 0)

        response = get(client, primary, "/users/me/intro-templates")
        check("自我介绍模板", response.status_code == 200 and len(response.json()) > 0)

        response = get(client, primary, "/profile/tag-options")
        check("固定标签选项", response.status_code == 200)

        response = get(client, primary, "/users/me/presence")
        check("在线状态", response.status_code == 200, response.text[:100])

        response = get(client, primary, "/auth/registration-intent")
        check("注册身份", response.status_code in (200, 204), response.text[:100])

        response = get(client, primary, "/auth/agreements")
        check("协议版本", response.status_code == 200, response.text[:120])

        response = get(client, primary, "/app/version", params={"platform": "mp-weixin", "version": "1.0.0"})
        check("版本信息", response.status_code in (200, 404), f"status={response.status_code}")

        # ---- 用第二个账号验证跨用户可见性 ----
        response = get(client, secondary, "/discovery/recommendations")
        items = response.json().get("items", []) if response.status_code == 200 else []
        check("第二个账号也能看到推荐", response.status_code == 200 and len(items) > 0,
              f"命中 {len(items)} 张名片")

        response = get(client, secondary, "/community/posts", params={"mode": "latest"})
        total = response.json().get("total", 0) if response.status_code == 200 else -1
        check("第二个账号社区流", response.status_code == 200 and total > 0, f"total={total}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 70)
    print(f"通过 {passed}/{total}")
    failures = [(name, detail) for name, ok, detail in results if not ok]
    if failures:
        print("\n未通过项：")
        for name, detail in failures:
            print(f"  - {name}: {detail[:200]}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
