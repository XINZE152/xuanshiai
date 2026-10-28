"""客源 SELECT 列注入的回归测试（2026-10-24 / 2026-10-28 扩展）。

`_lead_select_for` 用 ``\bFROM customer_lead\b`` 词边界只在外层表引用前补
gender 与已存在的本人资料列。客源标签功能下线后 LEAD_SELECT 不再含 tags_raw
标量子查询，本测试验证注入仍只发生一次且不破坏基表；并覆盖 birth_date→age
派生、缺列降级、photos 资料列注入。
"""

from app.services.customer_lead_admin import LEAD_SELECT, _lead_select_for


def test_gender_injected_exactly_once() -> None:
    sql = _lead_select_for(frozenset({"gender"}))
    assert sql.count("CAST(gender AS SIGNED) AS gender") == 1


def test_base_select_untouched_without_columns() -> None:
    # 无任何可选列时原样返回基表，不含 gender / age / 资料列
    assert _lead_select_for(frozenset()) == LEAD_SELECT
    assert "gender" not in LEAD_SELECT


def test_injection_only_touches_outer_from() -> None:
    # 资料列注入后，FROM customer_lead 仍只出现一次（子查询等不受影响）
    sql = _lead_select_for(frozenset({"gender", "real_name", "photos"}))
    assert sql.count("FROM customer_lead") == 1
    assert "real_name" in sql and "photos" in sql


def test_age_derived_from_birth_date() -> None:
    # 有 birth_date 时用 TIMESTAMPDIFF 派生 age，不再输出 raw age 列（避免重名）
    sql = _lead_select_for(frozenset({"birth_date", "age"}))
    assert "TIMESTAMPDIFF(YEAR, birth_date, UTC_DATE()) AS age" in sql
    assert ", age " not in sql and not sql.rstrip().endswith("age")


def test_age_falls_back_to_raw_column() -> None:
    # 无 birth_date 时回退 filters 迁移补的 raw age 列
    sql = _lead_select_for(frozenset({"age"}))
    assert "AS age" not in sql
    assert sql.count(", age FROM customer_lead") == 1
