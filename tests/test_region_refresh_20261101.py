"""属地刷新（A12）：清洗、格式化、号段解析与 provider 门控契约。

刻意**不依赖真实 IP 库**（10MB+ 外部资产，CI 与开发沙箱均没有），只覆盖可离线验证的部分：
清洗规则、字段下标格式化、号段解析、门控字面量、schema 默认值。
真实库文件的端到端验证见
``no upload/docs/一键更新属地-详细修改方案-2026-11-01.md`` §8.3 手工验收清单。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_args

import pytest

from app.api.routes import matchmaker_crm_admin as crm
from app.core.config import Settings, settings
from app.services.region import ip_offline, phone_offline, refresh as region_refresh, registry
from app.services.region.types import RegionDataNotReady, RegionKind

SAMPLE_SEGMENT = Path(__file__).resolve().parents[1] / "data" / "region" / "phone_segment.sample.csv"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "abc",
        "1.2.3",
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "240e:87c:892::1",
    ],
)
def test_clean_ip_rejects_unusable(raw: str | None) -> None:
    assert ip_offline.clean_ip(raw) is None


def test_clean_ip_accepts_public_and_strips_noise() -> None:
    assert ip_offline.clean_ip("112.23.179.205") == "112.23.179.205"
    assert ip_offline.clean_ip(" 112.23.179.205:5678 ") == "112.23.179.205"
    assert ip_offline.clean_ip("112.23.179.205, 10.0.0.1") == "112.23.179.205"
    assert ip_offline.clean_ip('"112.23.179.205"') == "112.23.179.205"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13812345678", "13812345678"),
        ("+86 138-1234-5678", "13812345678"),
        ("008613812345678", "13812345678"),
        ("8613812345678", "13812345678"),
        (None, None),
        ("", None),
        ("1381234567", None),
        ("12812345678", None),
        ("23812345678", None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert phone_offline.normalize_phone(raw) == expected


def test_format_region_handles_version_layouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_city_index", 2)
    # 开源 3.x：国家|省份|城市|ISP
    assert ip_offline.format_region("中国|江苏省|南京市|电信") == "江苏省南京市"
    # v1/v2：占位的 0 段必须被过滤
    assert ip_offline.format_region("中国|0|江苏省|南京市|电信") == "江苏省南京市"
    # 境外：只保留国家名
    assert ip_offline.format_region("美国|加利福尼亚|洛杉矶|ISP") == "美国"
    # 商用满载版：多出大洲段，城市下标后移一位
    monkeypatch.setattr(settings, "region_ip_city_index", 3)
    assert ip_offline.format_region("亚洲|中国|广东|深圳|福田|电信|114.05|22.55") == "广东深圳"


def test_format_region_parts_normalizes_short_api_names() -> None:
    assert ip_offline.format_region_parts("中国", "浙江", "杭州") == "浙江省杭州市"
    assert ip_offline.format_region_parts("中国", "北京", "北京") == "北京市"
    assert ip_offline.format_region_parts("中国", "内蒙古", "呼和浩特") == "内蒙古自治区呼和浩特市"
    assert ip_offline.format_region_parts("中国", "香港", "香港") == "香港特别行政区"
    assert ip_offline.format_region_parts("美国", "California", "Los Angeles") == "美国"


def test_phone_segment_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_phone_segment_path", str(SAMPLE_SEGMENT))
    outcome = phone_offline.resolve_many(
        ["13812345678", "13800001234", "17012345678", "12912345678", ""]
    )
    assert outcome.resolved["13812345678"] == "江苏省南京市"
    assert outcome.resolved["13800001234"] == "北京市"  # 直辖市省=市，不重复拼接
    assert outcome.resolved["17012345678"] == "广东省深圳市"
    assert outcome.skipped == 2  # 「129…」非法 + 空串
    assert outcome.unknown == 0


def test_phone_segment_missing_file_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_phone_segment_path", str(SAMPLE_SEGMENT.parent / "missing.csv"))
    with pytest.raises(RegionDataNotReady):
        phone_offline.resolve_many(["13812345678"])


def test_ip_db_missing_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_ip_db_path", str(SAMPLE_SEGMENT.parent / "missing.xdb"))
    assert ip_offline.is_ready() is False
    with pytest.raises(RegionDataNotReady):
        ip_offline.resolve_many(["112.23.179.205"])


def test_provider_enum_allows_offline_and_defaults_disabled() -> None:
    field = Settings.model_fields["region_resolver_provider"]
    assert field.default == "disabled"
    assert "offline" in get_args(field.annotation)


def test_registry_rejects_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "region_resolver_provider", "disabled")
    assert registry.refresh_enabled() is False
    with pytest.raises(RegionDataNotReady):
        registry.resolve(RegionKind.PHONE, ["13812345678"])


def test_kind_maps_to_internal_columns() -> None:
    assert RegionKind.IP.column == "ip_region"
    assert RegionKind.IP.source_column == "register_ip"
    assert RegionKind.PHONE.column == "phone_region"
    assert RegionKind.PHONE.source_column == "phone"


def test_refresh_region_keeps_gating_literals() -> None:
    """路由层保持门控字面量，写库语句只允许出现在 services/region/refresh.py。"""
    src = inspect.getsource(crm._refresh_region)
    assert "provider_for" in src
    assert "503" in src and "api" in src
    assert "dry_run=True" in src
    assert "UPDATE users SET ip_region" not in src
    assert "UPDATE users SET phone_region" not in src


def test_refresh_result_backward_compatible_defaults() -> None:
    result = crm.RegionRefreshResult(
        kind="ip",
        provider="api",
        dry_run=False,
        scanned_count=10,
        updated_count=3,
        adapter_implemented=True,
    )
    assert result.skipped_count == 0
    assert result.unknown_count == 0
    assert result.failed_count == 0
    assert result.overwrite is False
    assert result.affects_all is False
    assert result.elapsed_ms == 0


def test_resolver_status_has_scope_fields() -> None:
    status = crm.RegionResolverStatus(
        provider="disabled", refresh_enabled=True, adapter_implemented=True
    )
    assert status.scope == ""
    assert status.affects_all is False
    assert status.ip_db_ready is False
    assert status.phone_segment_ready is False
    assert status.ip_api_ready is False
    assert status.phone_api_ready is False
    assert status.db_version is None


class _AuditDb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.committed = False

    async def execute(self, statement: object, params: dict[str, object] | None = None) -> None:
        self.calls.append((str(statement), dict(params or {})))

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_audit_fill_empty_mode_records_changed_user_ids() -> None:
    db = _AuditDb()
    stats = region_refresh.RefreshStats(
        scanned=2,
        updated=2,
        overwritten=[
            {"user_id": 11, "old": None, "new": "江苏省南京市"},
            {"user_id": 12, "old": "", "new": "广东省深圳市"},
        ],
    )
    await region_refresh.write_audit(
        db, actor_id=5, kind=RegionKind.PHONE, stats=stats, overwrite=False, scope_label="ALL"
    )

    assert db.committed is True
    assert len(db.calls) == 1
    _, params = db.calls[0]
    assert "只填空" in str(params["reason"])
    assert '"user_id": 11' in str(params["after"])
    assert '"user_id": 12' in str(params["after"])


@pytest.mark.asyncio
async def test_audit_overwrite_mode_keeps_before_snapshot() -> None:
    db = _AuditDb()
    stats = region_refresh.RefreshStats(
        scanned=1,
        updated=1,
        overwritten=[{"user_id": 21, "old": "北京市", "new": "江苏省南京市"}],
    )
    await region_refresh.write_audit(
        db, actor_id=5, kind=RegionKind.IP, stats=stats, overwrite=True, scope_label="ALL"
    )

    _, params = db.calls[0]
    assert "覆盖" in str(params["reason"])
    assert '"old": "北京市"' in str(params["before"])


@pytest.mark.asyncio
async def test_audit_slices_large_changes_into_500_row_records() -> None:
    db = _AuditDb()
    stats = region_refresh.RefreshStats(
        scanned=1200,
        updated=1200,
        overwritten=[
            {"user_id": index, "old": None, "new": "江苏省南京市"} for index in range(1200)
        ],
    )
    await region_refresh.write_audit(
        db, actor_id=5, kind=RegionKind.PHONE, stats=stats, overwrite=False, scope_label="ALL"
    )

    assert len(db.calls) == 3
    assert "第 3 批" in str(db.calls[-1][1]["reason"])


@pytest.mark.asyncio
async def test_audit_without_changes_writes_one_summary() -> None:
    db = _AuditDb()
    stats = region_refresh.RefreshStats(scanned=3, updated=0)
    await region_refresh.write_audit(
        db, actor_id=5, kind=RegionKind.IP, stats=stats, overwrite=False, scope_label="ALL"
    )

    assert len(db.calls) == 1
    _, params = db.calls[0]
    assert "扫描 3 / 更新 0" in str(params["reason"])


@pytest.mark.asyncio
async def test_audit_partial_api_failure_adds_summary_row() -> None:
    db = _AuditDb()
    stats = region_refresh.RefreshStats(
        scanned=3,
        updated=1,
        failed=2,
        overwritten=[{"user_id": 31, "old": None, "new": "江苏省南京市"}],
    )
    await region_refresh.write_audit(
        db, actor_id=5, kind=RegionKind.IP, stats=stats, overwrite=False, scope_label="ALL"
    )

    assert len(db.calls) == 2
    assert "2 行 API 失败" in str(db.calls[-1][1]["reason"])
