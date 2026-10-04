from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_auth_script_references_present_controls():
    html = (ROOT / "mobile.html").read_text(encoding="utf-8")
    script = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
    html_ids = set(re.findall(r"\bid=[\"']([^\"']+)", html))
    referenced_ids = set(re.findall(r"byId\([\"']([^\"']+)", script))
    assert referenced_ids <= html_ids


def test_optional_fast_return_and_two_factor_controls_are_in_the_mobile_page():
    html = (ROOT / "mobile.html").read_text(encoding="utf-8")
    for control_id in (
        "mobile_remember_device",
        "mobile_fast_return",
        "mobile_totp_challenge",
        "mobile_totp_setup_form",
        "mobile_totp_confirm_form",
        "mobile_recovery_codes",
        "mobile_totp_disable_form",
    ):
        assert f'id="{control_id}"' in html


def test_totp_setup_cancel_handler_is_available_to_its_page_listener():
    script = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
    assert re.search(r"^  function cancelTotpSetup\(\)", script, re.MULTILINE)
    assert 'addEventListener("click", cancelTotpSetup)' in script


def test_showing_recovery_codes_clears_health_state_and_locks_navigation():
    script = (ROOT / "js/health_vault/mobile_consumer.js").read_text(encoding="utf-8")
    function = script.split("function displayRecoveryCodes(codes) {", 1)[1].split(
        "\n  }", 1
    )[0]
    assert "saveSession(null)" in function
    assert "clearAuthenticatedConsumerData()" in function
    assert "HCConsumerNav.setSecurityGate(true)" in function
