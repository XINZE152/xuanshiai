"""``ai_provider.parse_json`` 的容错与拒绝边界。

回归背景（2026-10-24 用户端测试）：``POST /api/v1/ai/profile/thoughtfulness``
返回 503「AI返回格式无效」。原实现是裸 ``json.loads``，模型输出只要带
Markdown 围栏、前后解释文字或尾逗号就整条判失败；再叠加 deepseek
``max_tokens`` 偏小导致的输出截断，失败概率进一步放大。

本文件锁定两件事：
1. 可恢复的噪声（围栏/说明文字/尾逗号）必须解析成功；
2. 不可恢复的输出（截断/顶层数组/空串）仍必须 503，不能放脏数据进业务层。
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from app.services.ai_provider import parse_json


@pytest.mark.parametrize(
    ("label", "payload", "expected"),
    [
        ("纯 JSON", '{"score":80,"summary":"ok"}', {"score": 80, "summary": "ok"}),
        ("markdown 围栏", '```json\n{"score":80}\n```', {"score": 80}),
        ("无语言标记的围栏", '```\n{"score":80}\n```', {"score": 80}),
        (
            "前后有解释文字",
            '好的，这是分析结果：\n{"score":80,"summary":"ok"}\n希望对你有帮助',
            {"score": 80, "summary": "ok"},
        ),
        ("对象尾逗号", '{"score":80,"todos":[{"key":"a"},],}', {"score": 80, "todos": [{"key": "a"}]}),
        # 边界：字符串值里出现花括号，不能被"截取首个 { 到最后一个 }"误伤
        (
            "字符串内含花括号",
            '{"summary":"用 { } 表达","score":1}',
            {"summary": "用 { } 表达", "score": 1},
        ),
        # 边界：围栏 + 前后说明 + 尾逗号同时出现
        (
            "三种噪声叠加",
            '结果如下：\n```json\n{"score":60,"todos":[],}\n```\n以上。',
            {"score": 60, "todos": []},
        ),
    ],
)
def test_parse_json_tolerates_common_model_noise(label: str, payload: str, expected: dict) -> None:
    assert parse_json(payload) == expected, label


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   ",
        "not-json",
        # 输出被 max_tokens 截断：缺少收尾括号
        '{"score":80,"summary":"你的资料还',
        # 顶层是数组：调用方随后会 data.get(...) 抛 AttributeError，必须在这里拦住
        '[1,2,3]',
        "null",
        '"just a string"',
    ],
)
def test_parse_json_rejects_unrecoverable_output(payload: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_json(payload)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "AI返回格式无效"


def test_parse_json_does_not_log_raw_content(caplog: pytest.LogCaptureFixture) -> None:
    """失败日志只记长度与首尾片段，不能把用户资料原文写进日志。"""
    secret = "身份证号110101199001011234"
    payload = '{"summary":"' + secret

    with pytest.raises(HTTPException):
        parse_json(payload)

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "ai_json_parse_failed" in joined
    assert "len=" in joined
    # reason 只带 json 模块的定位信息，不能因为"为了好排查"把原文写进去。
    body = joined.split("head=")[0]
    assert secret not in body


@pytest.mark.parametrize(
    ("payload", "expect_truncated"),
    [
        # 命中 max_tokens：以 { 开头、没有 } 收尾
        ('{"score":80,"summary":"你的资料还', True),
        ('{"score":80,"todos":[{"key":"a"', True),
        # 格式错乱而非截断：有收尾括号，中间的逗号/引号坏了
        ('{"score":80,,"summary":"ok"}', False),
        # 顶层数组：不是截断
        ("[1,2,3]", False),
    ],
)
def test_parse_json_logs_truncation_hint(
    payload: str, expect_truncated: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """失败日志要能直接分辨「被截断」和「格式错乱」，否则无法定因。"""
    caplog.set_level(logging.WARNING, logger="app.services.ai_provider")

    with pytest.raises(HTTPException):
        parse_json(payload)

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "ai_json_parse_failed" in joined
    assert f"truncated={str(expect_truncated).lower()}" in joined.lower()
    # reason 必须非空，便于确认是哪一层的 json 校验先失败
    assert "reason=" in joined
    assert "reason=no_candidate_parsed" not in joined


def test_parse_json_logs_json_error_position(caplog: pytest.LogCaptureFixture) -> None:
    """reason 要带 char 偏移，能直接对照 tail 判断断在哪里。"""
    caplog.set_level(logging.WARNING, logger="app.services.ai_provider")

    with pytest.raises(HTTPException):
        parse_json('{"score":80 "summary":"ok"}')

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "@char" in joined
