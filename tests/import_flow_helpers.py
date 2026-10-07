"""Test helper: consumer import through the server-enforced preview/confirm flow."""


def preview_and_confirm(client, headers, name, payload, mime="application/json"):
    preview = client.post(
        "/api/records/import-preview",
        headers=headers,
        files={"file": (name, payload, mime)},
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    assert token
    return client.post(f"/api/records/import-preview/{token}/confirm", headers=headers)
