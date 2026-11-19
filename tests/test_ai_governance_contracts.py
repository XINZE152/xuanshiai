from pathlib import Path

from tests._workspace_paths import require_frontend_root

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def find_workspace_root() -> Path:
    """定位存放 ``PRODUCT.md`` 的工作区根。

    ``PRODUCT.md`` 维护在前端仓库，后端单独 checkout 时它既不在后端根、也不一定在
    后端根的上级。解析顺序（环境变量 → 后端 ``.env`` → 逐级向上 → 同级
    ``xuanshiai-vue``）统一由 ``tests/_workspace_paths.py`` 实现，两处不要各写一份
    不同的查找规则。
    """
    return require_frontend_root(BACKEND_ROOT)


def test_ai_product_and_security_decisions_are_frozen() -> None:
    product = (find_workspace_root() / "PRODUCT.md").read_text(encoding="utf-8")
    decisions = (BACKEND_ROOT / "docs/ai/AI_PRODUCT_SECURITY_DECISIONS.md").read_text(
        encoding="utf-8"
    )
    product_required = (
        "AI 画像、搜索与资料合拍参考",
        "ai-policy-2026-08-07-v1",
    )
    decision_required = (
        "who_can_see_me=2 表示查看者 realname_status=2",
        "查看者认证状态缺失或无法判定时拒绝访问（fail-closed）",
        "AI 输出经用户确认后才能发布",
        "ai-policy-2026-08-20-v2",
        "compatibility-rule-v2",
        "legacy-rule-v1",
        "age",
        "city_code",
        "marriage_status",
        "education_level",
        "height_cm",
        "income_band",
        "occupation_group",
        "interest_tags",
        "lifestyle_tags",
        "relationship_goal",
        "profile_text_extract",
        "search_parse",
        "compatibility_shadow",
        "ai_policy_approved",
        "ai_provider_approved",
        "ai_retention_policy_version",
        "HTTP 503 AI_FEATURE_DISABLED",
        "retryable=false",
        "compatibility-rule-v2",
        "普通日志不得写入手机号、身份证、精确位置、原始 IP、原始 prompt、原始 Provider 响应、隐藏资料或凭据",
    )
    for phrase in product_required:
        assert phrase in product
    for source in (product, decisions):
        for phrase in decision_required:
            assert phrase in source
