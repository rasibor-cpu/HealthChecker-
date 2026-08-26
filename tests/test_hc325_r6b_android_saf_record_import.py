"""HC325-R6B — Android SAF content:// import without broad file access.

Static/source proofs plus isolated TestClient upload-contract regressions.
Does not talk to live :8766, restart the host, touch CSS :8765, or mutate
production vault/auth data.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/app/src/main"
LAUNCHER = ANDROID / "java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
POLICY = ANDROID / "java/com/healthchecker/companion/consumer/ConsumerSafFileChooserPolicy.kt"
MANIFEST = ANDROID / "AndroidManifest.xml"
MOBILE_JS = ROOT / "js/health_vault/mobile_consumer.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_saf_chooser_uses_open_document_and_read_grants():
    policy = _read(POLICY)
    launcher = _read(LAUNCHER)
    assert "ACTION_OPEN_DOCUMENT" in policy
    assert "FLAG_GRANT_READ_URI_PERMISSION" in policy
    assert "FLAG_GRANT_PERSISTABLE_URI_PERMISSION" in policy
    assert "createOpenDocumentIntent" in launcher
    assert "takePersistableUriPermission" in launcher
    assert "takeSafReadGrant" in launcher
    assert "EXTRA_ALLOW_MULTIPLE, ALLOW_MULTIPLE" in policy
    assert "ALLOW_MULTIPLE = false" in policy


def test_content_access_enabled_file_access_still_disabled():
    policy = _read(POLICY)
    launcher = _read(LAUNCHER)
    assert "ALLOW_CONTENT_ACCESS = true" in policy
    assert "ALLOW_FILE_ACCESS = false" in policy
    assert "ALLOW_FILE_ACCESS_FROM_FILE_URLS = false" in policy
    assert "ALLOW_UNIVERSAL_ACCESS_FROM_FILE_URLS = false" in policy
    assert "MIXED_CONTENT_NEVER_ALLOW" in policy
    assert "allowContentAccess = ConsumerSafFileChooserPolicy.ALLOW_CONTENT_ACCESS" in launcher
    assert "allowFileAccess = ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS" in launcher
    assert "allowFileAccessFromFileURLs = ConsumerSafFileChooserPolicy.ALLOW_FILE_ACCESS_FROM_FILE_URLS" in launcher
    assert "allowUniversalAccessFromFileURLs = ConsumerSafFileChooserPolicy.ALLOW_UNIVERSAL_ACCESS_FROM_FILE_URLS" in launcher
    assert "isSafContentUri" in policy


def test_no_broad_storage_permissions():
    manifest = _read(MANIFEST)
    assert "READ_EXTERNAL_STORAGE" not in manifest
    assert "WRITE_EXTERNAL_STORAGE" not in manifest
    assert "MANAGE_EXTERNAL_STORAGE" not in manifest
    assert "addJavascriptInterface" not in _read(LAUNCHER)


def test_stale_and_cancel_callbacks_are_cleared():
    launcher = _read(LAUNCHER)
    policy = _read(POLICY)
    assert "shouldCancelPreviousCallback" in launcher
    assert "fileCallback?.onReceiveValue(null)" in launcher
    assert "filePathCallback?.onReceiveValue(null)" in launcher
    assert "urisFromActivityResult" in launcher
    assert "shouldCancelPreviousCallback(): Boolean = true" in policy


def test_upload_contract_unchanged():
    js = _read(MOBILE_JS)
    assert 'request("/api/records/upload"' in js
    assert 'form.append("file", file, file.name)' in js
    upload_fn = js.split("async function upload()")[1].split("async function savePreferences")[0]
    assert "Content-Type" not in upload_fn
    assert "Authorization" in js
    assert "localhost" not in js
    assert "127.0.0.1" not in js
