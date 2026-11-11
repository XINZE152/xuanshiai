"""Q72: public banner payload exposes the configured visibility window."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.services import community as community_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Db:
    async def execute(self, statement, params=None):
        return _Result([
            {
                "id": 3,
                "title": "周末线下脱单局",
                "image_url": "https://example.com/banner.png",
                "link_type": "activity",
                "link_value": "42",
                "sort": 9,
                "position": "community",
                "start_at": datetime(2026, 11, 10, 0, 0, 0),
                "end_at": datetime(2026, 11, 16, 23, 59, 59),
            }
        ])


@pytest.mark.asyncio
async def test_banner_response_exposes_window() -> None:
    items = await community_service.list_banners(_Db(), position="community")
    assert len(items) == 1
    item = items[0]
    assert item.title == "周末线下脱单局"
    assert item.start_at == datetime(2026, 11, 10, 0, 0, 0)
    assert item.end_at == datetime(2026, 11, 16, 23, 59, 59)
