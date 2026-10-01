"""MS-A-05 回归：消息后台敏感字段分级脱敏。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.api.dependencies import CurrentMatchmakerAdmin
from app.schemas.matchmaker_admin import MatchmakerAdminAccount
from app.services.message_admin import (
    REDACTION_LEVEL_ELEVATED,
    REDACTION_LEVEL_STANDARD,
    redact_sensitive,
    redaction_level,
)

PHONE = "13812345678"
ID_CARD = "330106199001011234"
BANK_CARD = "6222021234567890"


def _admin(permissions: set[str]) -> CurrentMatchmakerAdmin:
    return CurrentMatchmakerAdmin(
        account=MatchmakerAdminAccount(
            id=1,
            username="admin",
            display_name="Admin",
            matchmaker_user_id=7,
            data_scope="ALL",
            organization_id=10,
            status=1,
            last_login_at=datetime(2026, 10, 4),
        ),
        session_id=1,
        permissions=frozenset(permissions),
    )


def test_redaction_level_follows_permissions() -> None:
    assert redaction_level(_admin({"message.read"})) == REDACTION_LEVEL_STANDARD
    assert redaction_level(_admin({"*"})) == REDACTION_LEVEL_ELEVATED
    assert redaction_level(_admin({"message.moderate"})) == REDACTION_LEVEL_ELEVATED
    assert redaction_level(_admin({"message.manage"})) == REDACTION_LEVEL_ELEVATED


def test_standard_level_masks_every_sensitive_field() -> None:
    text = f"手机{PHONE} 身份证{ID_CARD} 卡号{BANK_CARD} 微信号:abcde12345 地址:北京市朝阳区建国路88号"
    masked = redact_sensitive(text, REDACTION_LEVEL_STANDARD)
    assert masked is not None
    for raw in (PHONE, ID_CARD, BANK_CARD):
        assert raw not in masked
    assert "1**********" in masked
    assert "*" * len(ID_CARD) in masked
    assert "微信号:abc***" in masked
    assert "北京市朝阳区" + "*" * (len("北京市朝阳区建国路88号") - 6) in masked


def test_elevated_level_keeps_partial_but_never_full_value() -> None:
    text = f"手机{PHONE} 身份证{ID_CARD} 卡号{BANK_CARD}"
    masked = redact_sensitive(text, REDACTION_LEVEL_ELEVATED)
    assert masked is not None
    for raw in (PHONE, ID_CARD, BANK_CARD):
        assert raw not in masked
    assert "138****5678" in masked
    assert "3301" in masked and "1234" in masked


def test_elevated_never_reveals_full_phone_or_id() -> None:
    """即使最高等级也不得返回完整敏感值（仅首尾部分可见）。"""
    masked = redact_sensitive(PHONE, REDACTION_LEVEL_ELEVATED)
    assert masked != PHONE
    assert masked is not None and "*" in masked


def test_none_passes_through() -> None:
    assert redact_sensitive(None) is None


def test_wechat_id_is_masked() -> None:
    masked = redact_sensitive("联系我 wxid_ab12cd34ef", REDACTION_LEVEL_STANDARD)
    assert masked is not None
    assert "wxid_ab12cd34ef" not in masked
    assert masked.startswith("联系我 wxid_")


def test_ordered_patterns_do_not_double_mask_into_plaintext() -> None:
    """身份证先于银行卡匹配，掩码后不得再被当成卡号处理而漏出数字。"""
    masked = redact_sensitive(ID_CARD, REDACTION_LEVEL_STANDARD)
    assert masked is not None
    assert not any(char.isdigit() for char in masked)


def test_short_wechat_id_is_masked() -> None:
    """短微信号（原值组下界 {4,} 漏网的那一档）同样不得整串可见。"""
    for level in (REDACTION_LEVEL_STANDARD, REDACTION_LEVEL_ELEVATED):
        masked = redact_sensitive("微信号:ab1", level)
        assert masked == "微信号:***"
        assert "ab1" not in masked


def test_bare_wechat_id_with_intent_cue_is_masked() -> None:
    """无 ``wxid_`` 前缀也无「微信」标签的裸号，带接触意图线索词时必须掩掉。"""
    raw = "zhangsan88"
    standard = redact_sensitive(f"加我 {raw} 通过一下", REDACTION_LEVEL_STANDARD)
    elevated = redact_sensitive(f"加我 {raw} 通过一下", REDACTION_LEVEL_ELEVATED)
    assert standard is not None and elevated is not None
    assert raw not in standard and raw not in elevated
    assert "zha*******" in standard
    # elevated 只多保留尾 4 位，仍不是完整值
    assert "zha***an88" in elevated


def test_bare_pattern_does_not_mangle_plain_english() -> None:
    """裸号模式靠「须含数字或分隔符」抬高门槛，普通英文词正文不受影响。"""
    sentence = "加我 goodmorning 谢谢"
    assert redact_sensitive(sentence, REDACTION_LEVEL_STANDARD) == sentence


def test_wxid_prefix_never_full_at_elevated() -> None:
    """elevated 走 mask_middle(keep_tail=0)；早先的直连切片会拼回完整 wxid。"""
    raw = "wxid_ab12cd34ef"
    masked = redact_sensitive(f"联系我 {raw}", REDACTION_LEVEL_ELEVATED)
    assert masked is not None
    assert raw not in masked
    assert masked.startswith("联系我 wxid_")


@pytest.mark.parametrize("text", [
    "微信号:zhang_san88",
    "我的vx是 li_hua2024",
    "加我 zhaoshang99",
    "联系 wxid_qw12345678",
])
def test_no_writing_of_wechat_id_survives_either_level(text: str) -> None:
    """审计第 1 项判据：任一写法在任一等级下都不返回完整微信号。"""
    tokens = [t for t in ("zhang_san88", "li_hua2024", "zhaoshang99", "wxid_qw12345678") if t in text]
    assert len(tokens) == 1
    for level in (REDACTION_LEVEL_STANDARD, REDACTION_LEVEL_ELEVATED):
        masked = redact_sensitive(text, level)
        assert masked is not None
        assert tokens[0] not in masked
        assert "*" in masked
