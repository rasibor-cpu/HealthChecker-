"""HC-358 — server-enforced parse -> preview -> confirm -> commit protocol."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.health_vault.api import (
    _read_request_body_limited,
    create_health_vault_app,
)
from backend.health_vault.import_preview import ImportPreviewService, PreviewError
from backend.health_vault.vault_store import VaultStore


@pytest.fixture
def env(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    app = create_health_vault_app(
        store, production=False, test_users={"patient-A": "correct", "patient-B": "correct"}
    )
    client = TestClient(app)

    def login(user):
        r = client.post("/api/auth/login", json={"patient_id": user, "password": "correct"})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return store, client, app, login("patient-A"), login("patient-B")


def _payload(value=5.8, when="2026-08-16T10:00:00Z"):
    return json.dumps(
        {
            "source": "synthetic_lab",
            "measured_at": when,
            "extracted_measurements": [
                {"metric": "glucose", "value": value, "units": "mmol/L", "flag": "normal"}
            ],
        }
    ).encode()


def _preview(client, headers, payload=None, name="lab.json"):
    return client.post(
        "/api/records/import-preview",
        headers=headers,
        files={"file": (name, _payload() if payload is None else payload, "application/json")},
    )


def _docs(store):
    return len(store.list_documents())


def test_preview_requires_authentication(env):
    _, client, *_ = env
    assert _preview(client, {}).status_code == 401


def test_preview_creates_no_vault_record_and_returns_summary(env):
    store, client, _, a, _ = env
    r = _preview(client, a)
    assert r.status_code == 200
    body = r.json()
    assert _docs(store) == 0
    assert body["eligible"] is True and body["observation_count"] >= 1
    assert body["filename"] == "lab.json"
    assert len(body["preview_token"]) >= 40
    assert "storage" not in json.dumps(body).lower()
    assert _preview(client, a).json()["preview_token"] != body["preview_token"]


def test_direct_upload_is_rejected_and_commits_nothing(env):
    store, client, _, a, _ = env
    r = client.post(
        "/api/records/upload",
        headers=a,
        files={"file": ("lab.json", _payload(), "application/json")},
    )
    assert r.status_code == 428
    assert r.json()["code"] == "PREVIEW_CONFIRMATION_REQUIRED"
    assert _docs(store) == 0


def test_confirm_commits_and_retry_is_idempotent(env):
    store, client, _, a, _ = env
    token = _preview(client, a).json()["preview_token"]
    first = client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    assert first.status_code == 200 and first.json()["ok"] is True
    assert _docs(store) == 1
    retry = client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    assert retry.status_code == 200
    assert retry.json()["document_id"] == first.json()["document_id"]
    assert retry.json()["already_confirmed"] is True
    assert _docs(store) == 1


def test_cancel_leaves_vault_unchanged_and_blocks_confirm(env):
    store, client, _, a, _ = env
    token = _preview(client, a).json()["preview_token"]
    assert client.post(f"/api/records/import-preview/{token}/cancel", headers=a).status_code == 200
    r = client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    assert r.status_code == 409 and r.json()["code"] == "preview_cancelled"
    assert _docs(store) == 0


def test_other_user_and_forged_tokens_rejected(env):
    store, client, _, a, b = env
    token = _preview(client, a).json()["preview_token"]
    for headers, tok in ((b, token), (a, "forged-token"), ({}, token)):
        r = client.post(f"/api/records/import-preview/{tok}/confirm", headers=headers)
        assert r.status_code in (401, 404)
    assert client.post(f"/api/records/import-preview/{token}/cancel", headers=b).status_code == 404
    assert _docs(store) == 0
    assert client.post(f"/api/records/import-preview/{token}/confirm", headers=a).status_code == 200


def test_expired_token_cannot_commit(env):
    store, client, app, a, _ = env
    service = app.state.import_previews
    now = [1000.0]
    service._clock = lambda: now[0]
    token = _preview(client, a).json()["preview_token"]
    now[0] += service.ttl_seconds + 1
    r = client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    assert r.status_code in (404, 410)
    assert _docs(store) == 0


def test_tampered_staged_content_is_rejected(env):
    store, client, app, a, _ = env
    token = _preview(client, a).json()["preview_token"]
    app.state.import_previews._sessions[token].content = _payload(value=99)
    r = client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    assert r.status_code == 409 and r.json()["code"] == "preview_content_mismatch"
    assert _docs(store) == 0


def test_duplicate_preview_is_not_eligible(env):
    store, client, _, a, _ = env
    token = _preview(client, a).json()["preview_token"]
    client.post(f"/api/records/import-preview/{token}/confirm", headers=a)
    again = _preview(client, a).json()
    assert again["duplicate"] is True and again["eligible"] is False
    assert again["preview_token"] is None
    assert _docs(store) == 1


def test_unparseable_empty_file_rejected(env):
    _, client, _, a, _ = env
    r = _preview(client, a, payload=b"")
    assert r.status_code == 400


def test_concurrent_confirms_on_same_token_commit_once(env):
    store, client, app, a, _ = env
    token = _preview(client, a).json()["preview_token"]
    service = app.state.import_previews
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: service.confirm("patient-A", token), range(8)))
    assert len({r["document_id"] for r in results}) == 1
    assert sum(1 for r in results if not r["already_confirmed"]) == 1
    assert _docs(store) == 1


def test_concurrent_confirms_of_identical_previews_do_not_duplicate(env):
    store, client, app, a, _ = env
    service = app.state.import_previews
    tokens = [_preview(client, a).json()["preview_token"] for _ in range(6)]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda t: service.confirm("patient-A", t), tokens))
    assert _docs(store) == 1


def test_concurrent_distinct_files_all_commit_with_intact_index(env):
    store, client, app, a, _ = env
    service = app.state.import_previews
    tokens = [
        _preview(client, a, _payload(value=5 + i, when=f"2026-08-{10 + i}T10:00:00Z"), f"f{i}.json").json()[
            "preview_token"
        ]
        for i in range(6)
    ]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda t: service.confirm("patient-A", t), tokens))
    assert _docs(store) == 6
    assert len(store._read_index()["documents"]) == 6


def test_failed_commit_leaves_preview_retryable_and_vault_clean():
    calls = {"n": 0}

    def commit(*_a):
        calls["n"] += 1
        return {"ok": calls["n"] > 1, "document_id": "d1"}

    svc = ImportPreviewService(
        lambda *_a: {"ok": True, "measurements": [{"metric": "x"}], "errors": []}, commit
    )
    token = svc.create("u", b"abc", "f.json", "application/json")["preview_token"]
    with pytest.raises(PreviewError) as err:
        svc.confirm("u", token)
    assert err.value.code == "import_failed"
    assert svc.confirm("u", token)["document_id"] == "d1"
    assert calls["n"] == 2



class _ChunkedRequest:
    def __init__(self, chunks, content_length=None):
        self._chunks = list(chunks)
        self.yielded = 0
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = str(content_length)

    async def stream(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


def test_fallback_body_reader_stops_at_streaming_limit():
    request = _ChunkedRequest([b"1234", b"5678", b"should-not-be-read"])
    with pytest.raises(ValueError, match="^file_too_large$"):
        asyncio.run(_read_request_body_limited(request, 6))
    assert request.yielded == 2


def test_fallback_body_reader_rejects_oversized_content_length_before_streaming():
    request = _ChunkedRequest([b"should-not-be-read"], content_length=7)
    with pytest.raises(ValueError, match="^file_too_large$"):
        asyncio.run(_read_request_body_limited(request, 6))
    assert request.yielded == 0


def test_fallback_body_reader_accepts_payload_at_limit():
    request = _ChunkedRequest([b"123", b"456"], content_length=6)
    assert asyncio.run(_read_request_body_limited(request, 6)) == b"123456"
    assert request.yielded == 2
