"""
HC329 — guardrail against silent drift between the two independently
maintained clinical threshold tables:

- backend/health_vault/config/clinical_rules.json (authoritative)
- js/health_vault/clinical_rules.js (browser mirror, documented subset)

The browser copy exists so the WebView can classify a value without a round
trip to the server, but nothing previously enforced that its numbers stay in
sync with the authoritative JSON. This test parses both and asserts every
metric present in the JS mirror has bit-for-bit matching bands in the JSON.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS_TEXT = (ROOT / "js" / "health_vault" / "clinical_rules.js").read_text(encoding="utf-8")
JSON_RULES = json.loads(
    (ROOT / "backend" / "health_vault" / "config" / "clinical_rules.json").read_text(encoding="utf-8")
)["metrics"]

_METRIC_LINE = re.compile(r"^\s*(\w+):\s*\{(?P<body>[^{}]*)\},?\s*$", re.MULTILINE)
_FIELD = re.compile(r"(\w+):\s*(\[[^\]]*\]|-?\d+(?:\.\d+)?)")


def _parse_value(raw: str):
    raw = raw.strip()
    if raw.startswith("["):
        return [float(x) for x in raw[1:-1].split(",")]
    return float(raw)


def _parse_js_rules(js_text: str) -> dict[str, dict[str, object]]:
    # Only scan the RULES object body, not GUARDIAN_ABSOLUTE or anything after it.
    start = js_text.index("const RULES = {")
    end = js_text.index("\n  };", start)
    block = js_text[start:end]
    rules: dict[str, dict[str, object]] = {}
    for match in _METRIC_LINE.finditer(block):
        metric = match.group(1)
        body = match.group("body")
        fields = {k: _parse_value(v) for k, v in _FIELD.findall(body)}
        rules[metric] = fields
    return rules


def test_js_clinical_rules_mirror_matches_backend_json_for_shared_metrics():
    js_rules = _parse_js_rules(JS_TEXT)
    assert js_rules, "failed to parse any metric out of js/health_vault/clinical_rules.js RULES"

    mismatches = []
    for metric, js_bands in js_rules.items():
        json_bands = JSON_RULES.get(metric)
        if json_bands is None:
            mismatches.append(f"{metric}: present in JS mirror but absent from backend clinical_rules.json")
            continue
        for band_name, js_value in js_bands.items():
            json_value = json_bands.get(band_name)
            if json_value != js_value:
                mismatches.append(
                    f"{metric}.{band_name}: js={js_value!r} json={json_value!r}"
                )

    assert not mismatches, (
        "js/health_vault/clinical_rules.js has drifted from "
        "backend/health_vault/config/clinical_rules.json:\n" + "\n".join(mismatches)
    )


def test_js_clinical_rules_mirror_is_a_true_subset_of_known_metrics():
    js_rules = _parse_js_rules(JS_TEXT)
    unknown = set(js_rules) - set(JSON_RULES)
    assert not unknown, f"JS mirror defines metrics unknown to backend config: {sorted(unknown)}"
