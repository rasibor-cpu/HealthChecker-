"""HC-332 — consumer uploads must not block runtime health probes."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "backend" / "health_vault" / "api.py"


def test_consumer_upload_routes_offload_processing_from_event_loop():
    source = API.read_text(encoding="utf-8")
    marker = '@app.post("/api/records/upload")'
    sections = source.split(marker)

    # Multipart and dependency-free fallback routes must both preserve API
    # liveness while parsing and storing a potentially expensive record.
    assert len(sections) >= 3
    for route in sections[1:3]:
        body = route.split("@app.", 1)[0]
        assert "await run_in_threadpool(" in body
        assert "records_service.upload_record" in body
        assert "result = records_service.upload_record(" not in body


def test_record_detail_trend_refresh_is_offloaded_from_event_loop():
    source = API.read_text(encoding="utf-8")
    route = source.split('@app.get("/api/records/{document_id}")', 1)[1]
    body = route.split("@app.", 1)[0]
    assert "record = await run_in_threadpool(" in body
    assert "records_service.get_record_details, pid, document_id" in body
    assert "record = records_service.get_record_details(" not in body
