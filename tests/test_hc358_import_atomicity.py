from __future__ import annotations

from tests.test_hc356_dashboard_freshness import _import, _pipeline
from backend.health_vault.vault_store import VaultStore


def _doc_count(store: VaultStore) -> int:
    return len(store.list_documents()) if hasattr(store, "list_documents") else len(
        store._read_index().get("documents", [])
    )


def test_duplicate_import_does_not_duplicate_records(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    pipe = _pipeline(store)
    first = _import(pipe, name="dup", measured_at="2026-01-01T00:00:00+00:00")
    assert first["ok"] is True
    before = _doc_count(store)
    second = _import(pipe, name="dup", measured_at="2026-01-01T00:00:00+00:00")
    assert second["duplicate"] is True
    assert _doc_count(store) == before


def test_post_commit_metadata_failure_does_not_report_import_failed(tmp_path, monkeypatch):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    pipe = _pipeline(store)

    def boom(*_a, **_k):
        raise OSError("simulated")

    monkeypatch.setattr(pipe, "_attach_confidence", boom)
    result = _import(pipe, name="post", measured_at="2026-01-01T00:00:00+00:00")
    assert result["ok"] is True
    assert any(w.startswith("confidence_persist_skipped") for w in result["warnings"])
    assert _doc_count(store) == 1


def test_store_failure_leaves_no_partial_document(tmp_path, monkeypatch):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    pipe = _pipeline(store)

    def boom(*_a, **_k):
        raise OSError("index write failed")

    monkeypatch.setattr(store, "_write_index", boom)
    result = _import(pipe, name="fail", measured_at="2026-01-01T00:00:00+00:00")
    assert result["ok"] is False
    monkeypatch.undo()
    assert _doc_count(store) == 0
