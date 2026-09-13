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
BRIDGE = ANDROID / "java/com/healthchecker/companion/consumer/ConsumerRecordImportBridge.kt"
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


def test_javascript_bridge_is_exactly_one_narrowly_scoped_import_reader():
    # HC329: ConsumerRecordImportBridge is now the one intentional JavaScript
    # interface on this WebView (see its class doc for why — Chromium can
    # fail to stream a content:// blob directly into a multipart upload
    # body). It must remain the *only* bridge, expose exactly one
    # parameterless method, and take no JS-supplied URI/path — it can only
    # ever read whatever the native file-chooser callback most recently
    # recorded, never an arbitrary location JS asks for.
    launcher = _read(LAUNCHER)
    bridge = _read(BRIDGE)
    assert launcher.count("addJavascriptInterface(") == 1
    assert '"HCNativeImport"' in launcher
    assert bridge.count("@JavascriptInterface") == 1
    assert "fun readSelectedRecordBase64(): String {" in bridge
    assert "pendingUriProvider: () -> Uri?" in bridge
    assert "classifyImportRead" in bridge
    assert "MAX_IMPORT_BYTES" in _read(POLICY)


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
    # HC329: the multipart body is now built from whichever source supplied
    # the bytes (native bridge Blob, or the <input> File as a fallback) —
    # both are normalized into a `payload` with `.blob`/`.name` before this
    # single append call, so the POST contract itself is unchanged.
    assert 'form.append("file", payload.blob, payload.name)' in js
    upload_fn = js.split("async function upload()")[1].split("async function savePreferences")[0]
    assert "Content-Type" not in upload_fn
    assert "Authorization" in js
    assert "localhost" not in js
    assert "127.0.0.1" not in js


def test_native_bridge_preferred_with_input_file_fallback():
    js = _read(MOBILE_JS)
    assert "readSelectedFileViaNativeBridge" in js
    assert "window.HCNativeImport" in js
    assert "readSelectedRecordBase64" in js
    # Falls back to the plain <input> File object when no bridge is installed
    # (e.g. desktop/browser development), so behavior outside the governed
    # WebView is unchanged.
    assert 'byId("mobile_record_file").files[0]' in js


def test_upload_errors_are_categorized_not_raw_fetch_failures():
    js = _read(MOBILE_JS)
    fn = js.split("function describeUploadError(error)")[1].split("async function upload()")[0]
    assert "no_file_selected" in fn
    assert "file_too_large" in fn
    assert "file_unreadable" in fn
    # Raw browser network-layer failures (TypeError, e.g. "Failed to fetch")
    # must not be shown to the user verbatim — this is exactly what the
    # HC329 device UAT failure surfaced.
    assert "error instanceof TypeError" in fn
    assert "Failed to fetch" not in fn
