"""HC-360 canonical production dependency-lock regressions."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = (ROOT / "requirements" / "production.txt").read_text(encoding="utf-8")

# Import root -> distribution name where they differ.
ALIASES = {"pil": "pillow", "yaml": "pyyaml", "fitz": "pymupdf"}
# Optional Gmail acquisition connector; not part of the Companion Host runtime.
OPTIONAL_IMPORTS = {
    ("backend/health_vault/acquisition/gmail_api_connector.py", "google"),
    ("backend/health_vault/acquisition/gmail_api_connector.py", "googleapiclient"),
}


def _locked(name: str) -> str | None:
    m = re.search(rf"(?im)^{re.escape(name)}==([\w.]+) \\\r?\n((?:[ \t]+.*\r?\n?)+)", LOCK)
    return m.group(1) if m else None


def test_lock_contains_encrypted_vault_runtime_matching_hc311_pin():
    pin = re.search(r"(?m)^cryptography==([\w.]+)", (ROOT / "requirements-hc311.txt").read_text())
    assert pin, "requirements-hc311.txt must pin cryptography"
    assert _locked("cryptography") == pin.group(1)
    block = re.search(rf"(?im)^cryptography=={re.escape(pin.group(1))} \\\r?\n((?:[ \t]+.*\r?\n?)+)", LOCK)
    assert block and block.group(1).count("--hash=sha256:") >= 1


def test_lock_contains_hc359_ocr_stack_with_hashes():
    for name in ("rapidocr", "onnxruntime", "pymupdf", "numpy", "opencv-python", "pillow"):
        assert _locked(name), f"{name} missing or unhashed in production lock"


def test_every_module_level_third_party_import_in_backend_is_locked():
    locked = {m.group(1).lower() for m in re.finditer(r"(?m)^([A-Za-z0-9_.\-]+)==", LOCK)}
    stdlib = set(sys.stdlib_module_names)
    missing: list[str] = []
    for path in (ROOT / "backend").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                if root in stdlib or root == "backend" or (rel, root) in OPTIONAL_IMPORTS:
                    continue
                dist = ALIASES.get(root.lower(), root.lower().replace("_", "-"))
                if dist not in locked:
                    missing.append(f"{rel}: {root}")
    assert not missing, f"imports not covered by requirements/production.txt: {missing}"
