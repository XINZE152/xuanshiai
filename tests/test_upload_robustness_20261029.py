"""④ 图片上传健壮性（2026-10-29）：损坏/截断/退化小图应返回 4xx，不得穿透成 500。

生产 traceback 坐实：坏 PNG（IDAT CRC 校验失败）令 PIL verify() 抛 SyntaxError，
而 _image_outputs/_fu_image_outputs 原仅捕获 (UnidentifiedImageError, OSError)，
SyntaxError 漏出 → 上传接口 500。修复后统一归一为 415。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image

from app.services.member_follow_up_admin import _fu_image_outputs
from app.services.profile import _image_outputs


def _valid_png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _broken_crc_png() -> bytes:
    png = bytearray(_valid_png())
    png[-6] ^= 0xFF  # 破坏末尾 chunk 的 CRC → verify() 抛 SyntaxError
    return bytes(png)


@pytest.mark.parametrize("fn", [_image_outputs, _fu_image_outputs])
def test_broken_crc_png_returns_415_not_500(fn) -> None:
    with pytest.raises(HTTPException) as exc:
        fn(_broken_crc_png())
    assert exc.value.status_code == 415


@pytest.mark.parametrize("fn", [_image_outputs, _fu_image_outputs])
def test_truncated_png_returns_415(fn) -> None:
    with pytest.raises(HTTPException) as exc:
        fn(_broken_crc_png()[:40])
    assert exc.value.status_code == 415


@pytest.mark.parametrize("fn", [_image_outputs, _fu_image_outputs])
def test_non_image_bytes_returns_415(fn) -> None:
    with pytest.raises(HTTPException) as exc:
        fn(b"this is definitely not an image payload at all")
    assert exc.value.status_code == 415


@pytest.mark.parametrize("fn", [_image_outputs, _fu_image_outputs])
def test_valid_1x1_png_still_succeeds(fn) -> None:
    # 正常（哪怕退化到 1×1）的合法图片仍应成功处理，不受健壮性收紧影响
    out = fn(_valid_png())
    assert len(out) == 2
    assert all(len(b) > 0 for b in out)
