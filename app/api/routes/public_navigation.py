"""Public display labels and icons for current mini-program navigation."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.admin_config import PublicNavigationConfig, PublicNavigationItem, PublicNavigationSection
from app.services.admin_config import get_config

router = APIRouter(prefix="/public/navigation")

ALLOWED_ITEMS: dict[str, set[str]] = {
    "tab_bar": {
        "pages/index/index", "pages/matchmaker/matchmaker", "pages/community/community",
        "pages/message/message", "pages/profile/profile",
    },
    "home_channels": {"plaza", "membership_zone"},
    "community_tabs": {"follow", "city", "discover"},
    "message_shortcuts": {
        "applications", "paper_plane", "dynamic_notice", "emotion_lab",
    },
    "profile_main": {
        "pagesSub/profileExtra/my-tasks", "pagesSub/profileExtra/my-moments",
        "pagesSub/emotionLab/emotion-lab", "pagesSub/profileExtra/my-posters",
        "pagesSub/community/my-activities", "pagesSub/profileExtra/my-date",
        "pagesSub/profileExtra/certification", "invite", "pagesSub/profileExtra/settings",
    },
    "profile_bottom": {
        "pagesSub/profileExtra/help", "pagesSub/about/about", "pagesSub/profileExtra/security",
    },
    "matchmaker_tabs": {"service", "custom", "news"},
}


@router.get("", response_model=PublicNavigationConfig, summary="读取小程序导航名称与图标")
async def public_navigation(db: AsyncSession = Depends(get_db)) -> PublicNavigationConfig:
    snapshot = await get_config(db, "platform_navigation")
    raw_sections = snapshot.config.get("sections", [])
    sections: list[PublicNavigationSection] = []
    if isinstance(raw_sections, list):
        for raw_section in raw_sections:
            if not isinstance(raw_section, dict):
                continue
            section_key = raw_section.get("key")
            if section_key not in ALLOWED_ITEMS:
                continue
            allowed = ALLOWED_ITEMS[section_key]
            raw_items = raw_section.get("items", [])
            items: list[PublicNavigationItem] = []
            if isinstance(raw_items, list):
                for raw_item in raw_items:
                    if not isinstance(raw_item, dict):
                        continue
                    item_id = raw_item.get("id") or raw_item.get("path")
                    name = raw_item.get("name")
                    icon = raw_item.get("icon")
                    icon_class = raw_item.get("iconClass") or raw_item.get("icon_class")
                    if item_id not in allowed or not isinstance(name, str) or not name.strip():
                        continue
                    items.append(PublicNavigationItem(
                        id=item_id,
                        name=name.strip()[:32],
                        icon=icon.strip()[:500] if isinstance(icon, str) and icon.strip() else None,
                        icon_class=icon_class.strip()[:128] if isinstance(icon_class, str) and icon_class.strip() else None,
                        visible=raw_item.get("show") is not False,
                    ))
            sections.append(PublicNavigationSection(key=section_key, items=items))
    return PublicNavigationConfig(version=snapshot.version, sections=sections)
