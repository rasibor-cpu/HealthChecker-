"""HC-360 runtime locality evidence for the HC-359 local OCR stack.

What this proves: during OCR of images, embedded-text PDFs and scanned PDFs, no
*Python-level* network operation (connect / DNS / HTTP request / RapidOCR model
download) is attempted, and every model asset needed is shipped in the
hash-locked wheel and integrity-pinned.

What it does NOT prove: that native code (ONNX Runtime, OpenCV, the OS) opened no
sockets, that no telemetry exists below the Python audit layer, or anything
about how packages/models were originally acquired. It is not an absolute
"zero network activity" claim; OS-level egress control remains a separate gate.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata as md
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("pymupdf")
rapidocr = pytest.importorskip("rapidocr")

from backend.health_vault import ocr as ocr_mod
from backend.health_vault.ocr import (
    RAPIDOCR_MODEL_SHA256,
    LocalFirstOCRProvider,
    RapidLocalVisionOCRProvider,
)
from tests.hc360_synthetic import png_lines, scanned_pdf, text_pdf

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = Path(rapidocr.__file__).resolve().parent / "models"


def test_model_pins_match_installed_files_and_wheel_record():
    record = {
        Path(str(f)).name: f"{f.hash.mode}={f.hash.value}"
        for f in md.files("rapidocr") or []
        if f.hash and "models" in Path(str(f)).parts
    }
    for name, pinned in RAPIDOCR_MODEL_SHA256.items():
        data = (MODEL_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == pinned
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        assert record.get(name) == f"sha256={digest}", "pin must equal the hash shipped in the locked wheel"


def test_missing_or_tampered_model_fails_closed_without_download(monkeypatch):
    import requests

    def no_network(*_a, **_k):
        raise AssertionError("model download attempted")

    monkeypatch.setattr(requests, "get", no_network)
    monkeypatch.setattr(
        ocr_mod, "RAPIDOCR_MODEL_SHA256",
        {**RAPIDOCR_MODEL_SHA256, "PP-OCRv6_rec_small.onnx": "0" * 64},
    )
    result = RapidLocalVisionOCRProvider().extract(png_lines(["Glucose 112 mg/dL"]), mime_type="image/png")
    assert result.text == "" and result.meta["reason"] == "local_ocr_unavailable"

    monkeypatch.setattr(ocr_mod, "RAPIDOCR_MODEL_SHA256", {"absent-model.onnx": "0" * 64})
    result = RapidLocalVisionOCRProvider().extract(png_lines(["Glucose 112 mg/dL"]), mime_type="image/png")
    assert result.meta["reason"] == "local_ocr_unavailable"


def test_engine_construction_and_inference_never_reach_rapidocr_http_download(monkeypatch):
    from rapidocr.utils import download_file

    def forbidden(*_a, **_k):
        raise AssertionError("RapidOCR HTTP model download attempted")

    # RapidOCR validates local files via DownloadFile.run; only the HTTP step is forbidden.
    monkeypatch.setattr(
        download_file.DownloadFile, "_make_http_request", classmethod(lambda cls, *a, **k: forbidden())
    )
    result = RapidLocalVisionOCRProvider().extract(png_lines(["Glucose 112 mg/dL"]), mime_type="image/png")
    assert result.meta["reason"] == "ok" and "112" in result.text


def test_ocr_succeeds_with_python_network_primitives_blocked_in_process(monkeypatch):
    def deny(*_a, **_k):
        raise AssertionError("network access attempted")

    for target, attr in (
        (socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"),
        (socket, "getaddrinfo"), (socket, "gethostbyname"),
    ):
        monkeypatch.setattr(target, attr, deny)
    provider = LocalFirstOCRProvider()
    assert provider.extract(png_lines(["Glucose 112 mg/dL"]), mime_type="image/png").meta["reason"] == "ok"
    assert provider.extract(text_pdf(["Creatinine 90 umol/L eGFR 70"]), mime_type="application/pdf").meta["reason"] == "embedded_pdf_text"
    assert provider.extract(scanned_pdf([["Glucose 112 mg/dL"]]), mime_type="application/pdf").meta["reason"] == "scanned_pdf_local_ocr"


_CHILD = textwrap.dedent(
    """
    import json, sys, socket
    NETWORK = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
               "socket.sendto", "socket.getnameinfo", "urllib.Request"}
    seen = []
    def hook(event, args):
        if event in NETWORK or event.startswith("http.client"):
            seen.append(event)
            raise RuntimeError("blocked:" + event)
    sys.addaudithook(hook)
    try:  # canary: prove the hook really intercepts a connection attempt
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    except Exception:
        pass
    canary = list(seen); del seen[:]
    from backend.health_vault.ocr import LocalFirstOCRProvider
    p = LocalFirstOCRProvider()
    out = {}
    for key, (path, mime) in json.loads(sys.argv[1]).items():
        r = p.extract(open(path, "rb").read(), mime_type=mime)
        out[key] = r.meta.get("reason")
    print("RESULT" + json.dumps({"canary": canary, "events": seen, "out": out}))
    """
)


def test_fresh_process_ocr_makes_no_python_level_network_calls(tmp_path):
    files = {
        "image": (png_lines(["Glucose 112 mg/dL"]), "image/png", "i.png"),
        "text_pdf": (text_pdf(["Creatinine 90 umol/L eGFR 70"]), "application/pdf", "t.pdf"),
        "scanned_pdf": (scanned_pdf([["Glucose 112 mg/dL"]]), "application/pdf", "s.pdf"),
    }
    spec = {}
    for key, (data, mime, name) in files.items():
        (tmp_path / name).write_bytes(data)
        spec[key] = [str(tmp_path / name), mime]
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "OC_DISABLE_DOT_ACCESS_WARNING": "1"}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, json.dumps(spec)],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=240,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, proc.stderr[-2000:]
    result = json.loads(line[len("RESULT"):])
    assert result["canary"], "audit hook failed to intercept the canary connection"
    assert result["events"] == []
    assert result["out"] == {"image": "ok", "text_pdf": "embedded_pdf_text", "scanned_pdf": "scanned_pdf_local_ocr"}
