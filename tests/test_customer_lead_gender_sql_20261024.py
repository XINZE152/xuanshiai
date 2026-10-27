"""A13-gender：客源 gender 列注入的回归测试（2026-10-24）。

`_apply_gender_column` 用 ``\bFROM customer_lead\b`` 词边界只在外层表引用前补
gender 列。客源标签功能下线后 LEAD_SELECT 不再含 tags_raw 标量子查询，
本测试验证注入仍只发生一次且不破坏基表。
"""

from app.services.customer_lead_admin import LEAD_SELECT, _apply_gender_column


def test_gender_injected_exactly_once() -> None:
    sql = _apply_gender_column(LEAD_SELECT)
    assert sql.count("CAST(gender AS SIGNED) AS gender") == 1


def test_base_select_untouched_without_gender() -> None:
    # 未触发注入时 LEAD_SELECT 本身不含 gender
    assert "gender" not in LEAD_SELECT
