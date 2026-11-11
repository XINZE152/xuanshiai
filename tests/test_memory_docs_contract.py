"""Memory Kernel Core v1 documentation contract tests (Task 1).

The workspace-level product contract (``PRODUCT.md``) and the architecture
note (``docs/architecture/ai-memory-kernel-core-v1.md``) must state — before
any kernel code ships — that:

- the kernel is shadow-only this phase: nothing is auto-published and no
  downstream consumer (Search / Compatibility / Recommend) switches over;
- the deferred scope is explicit (AI 军师 / AI 分身 namespace, frontend
  Memory View, Projection/ProjectionGrant, granular consent);
- subject isolation and the append-only ledger are product-level promises.
"""

from __future__ import annotations

from pathlib import Path

from tests._workspace_paths import FRONTEND_ENV, find_frontend_root

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 找不到时给出与解析顺序一致的兜底路径，让 _read 的断言报出具体缺哪个文件，
# 而不是在模块导入阶段整片报错、掩盖真正的缺失原因。
WORKSPACE_ROOT = find_frontend_root(BACKEND_ROOT) or (BACKEND_ROOT.parent / "xuanshiai-vue")
PRODUCT_MD = WORKSPACE_ROOT / "PRODUCT.md"
ARCHITECTURE_MD = WORKSPACE_ROOT / "docs" / "architecture" / "ai-memory-kernel-core-v1.md"


def _read(path: Path) -> str:
    assert path.exists(), (
        f"required document is missing: {path}"
        f"（文档在前端仓库；可在环境变量或后端 .env 中设置 {FRONTEND_ENV}=<前端仓库根>）"
    )
    return path.read_text(encoding="utf-8")


def test_product_md_declares_memory_kernel_shadow_scope() -> None:
    text = _read(PRODUCT_MD)
    assert "记忆内核" in text
    assert "Shadow Write" in text or "影子写入" in text
    assert "不自动发布" in text
    assert "personal" in text and "ideal_partner" in text
    assert "确认" in text and "删除" in text


def test_product_md_declares_deferred_scope() -> None:
    text = _read(PRODUCT_MD)
    assert "延期" in text
    assert "AI 军师" in text or "AI军师" in text
    assert "Memory View" in text


def test_architecture_doc_declares_frozen_contract() -> None:
    text = _read(ARCHITECTURE_MD)
    assert "append-only" in text
    assert "ai_memory_event" in text
    assert "personal" in text and "ideal_partner" in text
    assert "user_explicit" in text and "behavior" in text
    assert "valid_until" in text
    assert "Suppression" in text or "suppression" in text
    assert "canonical" in text


def test_architecture_doc_declares_no_downstream_switch_and_privacy_floor() -> None:
    text = _read(ARCHITECTURE_MD)
    assert "不切换" in text
    assert "Search" in text and "Compatibility" in text
    assert "transcript" in text
    assert "512" in text


def test_architecture_doc_declares_subject_write_guard() -> None:
    text = _read(ARCHITECTURE_MD)
    assert "fact_kind" in text
    assert "about_user" in text and "partner_preference" in text
