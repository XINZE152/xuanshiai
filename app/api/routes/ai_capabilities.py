"""运行时 AI 能力探测。

背景：AI 各能力默认关闭（``AI_MASTER_ENABLED`` 等一律 False），但前端入口
照常展示，用户点进去才收到 503，表现为「AI 搜索不可用 / 画像不可用 / 语音
不可用」三种互不相干的故障。本接口把「此刻哪些能力真的能用」一次性下发，
判定直接复用 ``require_ai_feature``，不在前端复制开关逻辑（避免两处漂移）。
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentUser, get_current_user
from app.core.config import settings
from app.schemas.ai_common import AiCapabilitiesResponse
from app.services.ai.flags import AiFeature, AiFeatureDisabledError, require_ai_feature

router = APIRouter()


def _usable(feature: AiFeature) -> bool:
    """True 当且仅当此刻调用该能力的接口不会因门禁返回 503。"""
    try:
        require_ai_feature(feature, settings)
    except AiFeatureDisabledError:
        return False
    return True


@router.get(
    "/capabilities",
    response_model=AiCapabilitiesResponse,
    summary="查询当前登录用户可用的 AI 能力",
)
async def get_ai_capabilities(
    _current: CurrentUser = Depends(get_current_user),
) -> AiCapabilitiesResponse:
    """返回各 AI 能力此刻是否可用；``master=false`` 时其余字段恒为 false。"""
    master = bool(settings.ai_master_enabled)
    return AiCapabilitiesResponse(
        master=master,
        profile=_usable(AiFeature.PROFILE),
        search=_usable(AiFeature.SEARCH),
        compatibility=_usable(AiFeature.COMPATIBILITY_SHADOW),
        voice=_usable(AiFeature.VOICE),
        advisor=_usable(AiFeature.ADVISOR),
        avatar=_usable(AiFeature.AVATAR),
    )
