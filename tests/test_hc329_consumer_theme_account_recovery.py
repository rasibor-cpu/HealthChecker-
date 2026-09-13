from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_defaults_to_hospital_blue_and_keeps_dark_option():
    html = _read("mobile.html")
    css = _read("style.css")

    assert '<body class="mobile-consumer light-theme">' in html
    assert '<option value="light">Hospital Blue</option>' in html
    assert '<option value="dark">Dark</option>' in html
    assert "HC329 - clinical Hospital Blue consumer theme" in css
    assert "body.mobile-consumer.light-theme" in css
    assert "--bg: #eef7fb;" in css
    assert "--accent: #1976a3;" in css


def test_mobile_exposes_password_change_and_lost_password_recovery():
    html = _read("mobile.html")
    js = _read("js/health_vault/mobile_consumer.js")

    assert "Forgot password? Reset it securely" in html
    assert "Recover your account" in html
    assert "Password &amp; recovery" in html
    assert "Change password" in html
    assert "Recovery questions" in html

    for endpoint in (
        "/api/auth/password/change",
        "/api/auth/recovery/catalog",
        "/api/auth/recovery/start",
        "/api/auth/recovery/verify",
        "/api/auth/recovery/complete",
        "/api/auth/recovery/enroll",
    ):
        assert endpoint in js


def test_recovery_copy_does_not_claim_email_or_sms_when_not_implemented():
    html = _read("mobile.html").lower()

    assert "email reset link" not in html
    assert "sms reset" not in html
    assert "text message reset" not in html
