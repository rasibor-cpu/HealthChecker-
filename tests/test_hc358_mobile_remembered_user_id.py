"""HC-358 port: /mobile remembers only the non-secret User ID (native, encrypted)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
HTML = (ROOT / "mobile.html").read_text(encoding="utf-8")
PREFS = (ROOT / "android/app/src/main/java/com/healthchecker/companion/secure/SecurePrefs.kt").read_text(
    encoding="utf-8"
)
LAUNCHER = (
    ROOT / "android/app/src/main/java/com/healthchecker/companion/ui/ConsumerLauncherActivity.kt"
).read_text(encoding="utf-8")


def _function(source: str, name: str, end: str = "\n  }\n") -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index(end, start)]


def _kotlin_function(name: str) -> str:
    start = PREFS.index(f"fun {name}(")
    return PREFS[start:PREFS.index("\n\n", start)]


def test_login_offers_opt_in_remembered_user_id():
    assert 'id="mobile_remember_user_id"' in HTML
    assert "never your password" in HTML
    assert 'id="mobile_user_id" name="username" autocomplete="username"' in HTML
    assert 'type="password" autocomplete="current-password"' in HTML


def test_successful_login_stores_only_the_user_id_after_authentication():
    save = _function(JS, "saveRememberedUserId")
    assert "bridge.saveRememberedUserId(userId)" in save
    assert "bridge.clearRememberedUserId()" in save
    assert not re.search(r"password|token|totp|pin", save, re.I)
    login = _function(JS, "login")
    assert login.index('await request("/api/auth/login"') < login.index("saveRememberedUserId(userId)")
    assert login.index("saveRememberedUserId(userId)") < login.index("finishLogin(body)")


def test_logout_destroys_session_but_keeps_remembered_id():
    logout = _function(JS, "logout")
    assert "saveSession(null)" in logout
    assert "clearAuthenticatedConsumerData()" in logout
    assert "RememberedUserId" not in logout
    assert "sessionStorage.removeItem(SESSION_KEY)" in JS
    assert "mobile_user_id" not in _function(JS, "clearAuthenticatedConsumerData")
    start = PREFS.index("fun clearUserScopedState()")
    clear_scoped = PREFS[start:PREFS.index("\n    }\n", start)]
    assert "KEY_REMEMBERED_USER_ID" not in clear_scoped
    assert "KEY_TRUSTED_DEVICE_TOKEN" in clear_scoped


def test_remembered_id_is_prefilled_on_return_to_login_without_secrets():
    load = _function(JS, "loadRememberedUserId")
    assert "bridge.readRememberedUserId()" in load
    assert 'byId("mobile_user_id").value = remembered' in load
    assert "mobile_password" not in load
    assert "loadRememberedUserId();" in JS


def test_remembered_id_is_native_validated_and_origin_gated():
    assert "localStorage" not in JS
    save = _kotlin_function("saveRememberedUserId")
    assert "REMEMBERED_USER_ID_PATTERN.matches" in save
    assert 'Regex("^[A-Za-z0-9._@-]{1,64}$")' in PREFS
    for name in ("readRememberedUserId", "saveRememberedUserId", "clearRememberedUserId"):
        body = LAUNCHER[LAUNCHER.index(f"fun {name}("):]
        body = body[:body.index("\n        }\n")]
        assert "isFirstPartyBridgeCall()" in body


def test_session_never_persisted_beyond_logout_so_next_user_is_unauthenticated():
    assert "sessionStorage.setItem(SESSION_KEY" in JS
    assert "sessionStorage.removeItem(SESSION_KEY)" in JS
