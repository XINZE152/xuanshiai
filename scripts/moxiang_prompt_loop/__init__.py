"""墨相师·提示词闭环验证入口包。

CLI 入口::

    python -m scripts.moxiang_prompt_loop.cli \
        --transcript tests/fixtures/moxiang/transcripts/30turn-baseline.jsonl \
        --provider dots \
        --prompt-versions master=v1.1,extract=v1.1,narrative=v4 \
        --account e2e-moxiang-test/account1.json \
        --out artifacts/moxiang-prompt-loop/2026-09-04T1030Z \
        --frontend-trace xuanshiai-vue/static/dev/moxiang-trace.json

不进入 Alembic、不写新数据库 schema、不挂生产代码。仅在
``settings.environment`` 为 dev / test / ci 时可运行。
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
