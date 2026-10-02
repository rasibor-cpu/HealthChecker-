from __future__ import annotations

from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from backend.health_vault.api import create_health_vault_app
from backend.health_vault.dashboard_service import DashboardService
from backend.health_vault.models import MedicalDocument
from backend.health_vault.vault_store import VaultStore

ROOT = Path(__file__).resolve().parents[1]


def _sample_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_text((24, 40), "Laboratory report preview")
    content = document.tobytes()
    document.close()
    return content


def _sample_png() -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800), False)
    pixmap.clear_with(240)
    content = pixmap.tobytes("png")
    pixmap = None
    return content


def _store_record(store: VaultStore, document: MedicalDocument, content: bytes) -> None:
    store.store(document=document, measurements=[], content=content)


def test_authenticated_thumbnail_renders_pdf_and_image_without_disk_cache(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"P" * 32)
    pdf_bytes = _sample_pdf()
    image_bytes = _sample_png()
    _store_record(
        store,
        MedicalDocument(
            id="pdf-preview",
            patient_id="patient-A",
            original_filename="laboratory.pdf",
            mime_type="application/pdf",
        ),
        pdf_bytes,
    )
    _store_record(
        store,
        MedicalDocument(
            id="image-preview",
            patient_id="patient-A",
            original_filename="scan.png",
            mime_type="image/png",
        ),
        image_bytes,
    )
    client = TestClient(
        create_health_vault_app(
            store,
            production=False,
            test_users={"patient-A": "correct", "patient-B": "correct"},
        )
    )
    token_a = client.post(
        "/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}
    ).json()["token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}
    token_b = client.post(
        "/api/auth/login", json={"patient_id": "patient-B", "password": "correct"}
    ).json()["token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    assert client.get("/api/records/thumbnail/pdf-preview").status_code == 401
    assert client.get("/api/records/thumbnail/pdf-preview", headers=headers_b).status_code == 404

    for document_id in ("pdf-preview", "image-preview"):
        response = client.get(
            f"/api/records/thumbnail/{document_id}",
            headers=headers_a,
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["vary"] == "Authorization"
        assert response.headers["x-content-type-options"] == "nosniff"
        with pymupdf.open(stream=response.content) as preview:
            page = preview.load_page(0)
            assert page.rect.width <= 480
            assert page.rect.height <= 640

    for document_id, original in (("pdf-preview", pdf_bytes), ("image-preview", image_bytes)):
        path = store.resolve_storage_path(f"vault://documents/{document_id}.bin")
        assert path is not None
        assert path.read_bytes() != original
        assert original not in path.read_bytes()


def test_thumbnail_uses_clean_fallback_for_unsupported_record(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"Q" * 32)
    _store_record(
        store,
        MedicalDocument(
            id="json-record",
            patient_id="patient-A",
            original_filename="measurements.json",
            mime_type="application/json",
        ),
        b'{"value": 42}',
    )
    client = TestClient(
        create_health_vault_app(
            store,
            production=False,
            test_users={"patient-A": "correct"},
        )
    )
    token = client.post(
        "/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}
    ).json()["token"]
    response = client.get(
        "/api/records/thumbnail/json-record",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "Preview unavailable"


def test_selected_file_preview_is_authenticated_temporary_and_non_mutating(tmp_path):
    store = VaultStore(root=tmp_path / "vault", encryption_key=b"R" * 32)
    pdf_bytes = _sample_pdf()
    image_bytes = _sample_png()
    client = TestClient(
        create_health_vault_app(
            store,
            production=False,
            test_users={"patient-A": "correct"},
        )
    )
    token = client.post(
        "/api/auth/login", json={"patient_id": "patient-A", "password": "correct"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    before_documents = store.list_documents()

    unauthorized = client.post(
        "/api/records/preview",
        files={"file": ("laboratory.pdf", pdf_bytes, "application/pdf")},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store, private"

    response = client.post(
        "/api/records/preview",
        headers=headers,
        files={"file": ("laboratory.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["vary"] == "Authorization"
    assert response.headers["x-content-type-options"] == "nosniff"
    with pymupdf.open(stream=response.content) as preview:
        page = preview.load_page(0)
        assert page.rect.width <= 480
        assert page.rect.height <= 640

    image_preview = client.post(
        "/api/records/preview",
        headers=headers,
        files={"file": ("scan.png", image_bytes, "image/png")},
    )
    assert image_preview.status_code == 200
    assert image_preview.headers["content-type"] == "image/png"
    assert image_preview.headers["cache-control"] == "no-store, private"

    unsupported = client.post(
        "/api/records/preview",
        headers=headers,
        files={"file": ("measurements.json", b'{"value": 42}', "application/json")},
    )
    assert unsupported.status_code == 415
    assert unsupported.headers["cache-control"] == "no-store, private"
    assert store.list_documents() == before_documents
    assert not list((tmp_path / "vault").rglob("laboratory.pdf"))


def test_dashboard_recent_record_is_ordered_by_receipt_not_clinical_date(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    received_later = MedicalDocument(
        id="received-later",
        patient_id="patient-A",
        original_filename="older-result.pdf",
        imported_at="2026-06-15T10:00:00Z",
        measured_at="2021-02-03T09:00:00Z",
        source_document_date="2021-02-04",
    )
    measured_later = MedicalDocument(
        id="measured-later",
        patient_id="patient-A",
        original_filename="newer-result.pdf",
        imported_at="2024-06-15T10:00:00Z",
        measured_at="2024-06-14T09:00:00Z",
        source_document_date="2024-06-15",
    )
    _store_record(store, received_later, b"%PDF-1.4 receipt fixture")
    _store_record(store, measured_later, b"%PDF-1.4 measurement fixture")

    summary = DashboardService(store).get_summary("patient-A")
    imported = next(widget.payload for widget in summary.widgets if widget.widget_id == "import_wizard")
    recent = imported["recent_records"][0]
    assert recent["document_id"] == "received-later"
    assert recent["imported_at"] == "2026-06-15T10:00:00Z"
    assert recent["measured_at"] == "2021-02-03T09:00:00Z"
    assert recent["source_document_date"] == "2021-02-04"


def test_mobile_dashboard_record_actions_and_preview_contract():
    html = (ROOT / "mobile.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "health_vault" / "mobile_consumer.js").read_text(encoding="utf-8")
    api = (ROOT / "backend" / "health_vault" / "api.py").read_text(encoding="utf-8")

    assert 'id="mobile_add_record"' in html and "+ ADD RECORD" in html
    assert 'id="mobile_view_last_record"' in html and "VIEW LAST RECORD" in html
    assert 'byId("mobile_add_record").addEventListener("click", () => showView("import"))' in script
    assert "recentReceivedRecord = recentRecords[0]" in script
    assert 'appendRecordMeta(content, "Document date"' in script
    assert 'appendRecordMeta(content, "Clinical date"' in script
    assert 'appendRecordMeta(content, "Received"' in script
    assert "cache: \"no-store\"" in script and "URL.revokeObjectURL" in script
    assert "img-src 'self' data: blob:" in api and "object-src 'none'" in api
    assert '<form id="mobile_login_form"' in html and 'name="username" autocomplete="username"' in html
    assert 'name="password" type="password" autocomplete="current-password"' in html
    assert "HealthChecker keeps only a session for this page and never saves your password." in html
    assert 'id="mobile_upload_review"' in html and 'id="mobile_upload_button" type="button" disabled' in html
    assert 'id="mobile_cancel_upload_review"' in html
    assert 'byId("mobile_record_file").addEventListener("change", reviewSelectedFile)' in script
    assert 'byId("mobile_upload_button").addEventListener("click", upload)' in script
    assert 'fetch("/api/records/preview"' in script
    assert 'request("/api/records/upload"' in script
    assert "No records have been received yet." in script
    assert "localStorage" not in script
    assert 'android:importantForAutofill="yes"' in (
        ROOT / "android" / "app" / "src" / "main" / "res" / "layout" / "activity_consumer_launcher.xml"
    ).read_text(encoding="utf-8")


def test_mobile_route_serves_versioned_consumer_assets(tmp_path):
    store = VaultStore(root=tmp_path / "vault", allow_plaintext=True)
    client = TestClient(create_health_vault_app(store, production=False))

    page = client.get("/mobile")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "+ ADD RECORD" in page.text
    assert "VIEW LAST RECORD" in page.text
    assert 'hc352_mobile_record_ux.css?v=hc344' in page.text
    assert 'mobile_consumer.js?v=hc344' in page.text

    stylesheet = client.get("/css/hc352_mobile_record_ux.css?v=hc344")
    script = client.get("/js/health_vault/mobile_consumer.js?v=hc344")
    assert stylesheet.status_code == 200 and ".mobile-upload-review" in stylesheet.text
    assert script.status_code == 200 and 'fetch("/api/records/preview"' in script.text
