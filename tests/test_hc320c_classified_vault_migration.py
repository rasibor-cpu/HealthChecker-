import argparse
import json

from backend.health_vault.auth import hash_password
from backend.health_vault.vault_crypto import MAGIC
from scripts import migrate_hc320c_classified_vault as migration
from scripts.migrate_hc320c_classified_vault import AMBIGUOUS_DOCUMENT, build_production_index


def _doc(document_id, patient_id, provenance="clinical"):
    return {"id": document_id, "patient_id": patient_id, "provenance": provenance}


def test_classified_transform_migrates_only_explicit_owner():
    source = {
        "documents": [
            _doc("robert", "00000"),
            _doc("s1", "default-patient", "synthetic"),
            _doc("s2", "default-patient", "fixture"),
            _doc("s3", "default-patient", "test"),
            _doc("s4", "default-patient", "demo"),
            _doc(AMBIGUOUS_DOCUMENT, "default-patient"),
        ],
        "measurements": [
            {"document_id": "robert", "patient_id": "00000"},
            {"document_id": AMBIGUOUS_DOCUMENT, "patient_id": "default-patient"},
        ],
        "imports": [],
        "import_log": [],
        "timeline_events": [{"patient_id": "default-patient"}],
        "health_intelligence": {"observations": [
            {"patient_id": "00000"}, {"patient_id": "default-patient"},
        ]},
        "alerts": [],
        "data_gaps": [],
        "encounters": [],
        "medications": [],
        "observations": [],
        "cgm_sensors": [],
        "batch_audits": [],
        "ai_import_audits": [],
        "guardian_audits": [],
        "monitoring_audits": [],
        "trends": {"00000": {"status": "owner"}, "default-patient": {"status": "ambiguous"}},
        "profiles_by_user_id": {"00000": {"diagnoses": [], "medications": []}},
    }
    target, counts = build_production_index(source)
    assert [row["id"] for row in target["documents"]] == ["robert"]
    assert len(target["measurements"]) == 1
    assert target["timeline_events"] == []
    assert len(target["health_intelligence"]["observations"]) == 1
    assert set(target["trends"]) == {"00000"}
    assert counts["excluded_documents"] == 4
    assert counts["quarantined_documents"] == 1


def test_atomic_migration_backup_and_restore_round_trip(tmp_path, monkeypatch):
    source, snapshot = tmp_path / "source", tmp_path / "snapshot"
    (source / "documents").mkdir(parents=True)
    source_data = {
        "schema_version": "hc.health_vault.v1",
        "documents": [_doc("robert", "00000")] + [
            _doc(f"s{number}", "default-patient", "synthetic") for number in range(1, 5)
        ] + [_doc(AMBIGUOUS_DOCUMENT, "default-patient")],
        "measurements": [], "imports": [], "import_log": [], "timeline_events": [],
        "health_intelligence": {"observations": []}, "alerts": [], "data_gaps": [],
        "encounters": [], "medications": [], "observations": [], "cgm_sensors": [],
        "batch_audits": [], "ai_import_audits": [], "guardian_audits": [],
        "monitoring_audits": [], "trends": {},
        "profiles_by_user_id": {"00000": {"diagnoses": [], "medications": []}},
    }
    (source / "index.json").write_text(json.dumps(source_data), encoding="utf-8")
    (source / "documents" / "robert.bin").write_bytes(b"explicit-owner-payload")
    registry = {"schema_version": "hc.auth.registry.v1", "accounts": {"00000": {
        "user_id": "00000", "name": "Robert Asibor", "email_identifier": "00000",
        "password_hash": hash_password("temporary"), "password_changed_at": None,
        "password_expiry_date": None, "must_change_password": True,
        "account_status": "password_change_required", "role": "owner",
    }}, "sessions": {}, "audit": []}
    (source / "auth_registry.json").write_text(json.dumps(registry), encoding="utf-8")
    import shutil
    shutil.copytree(source, snapshot)

    monkeypatch.setattr(migration, "write_protected_key", lambda path, key: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(key)))
    monkeypatch.setattr(migration, "read_protected_key", lambda path: path.read_bytes())
    args = argparse.Namespace(
        source=source, snapshot=snapshot, target=tmp_path / "production",
        key_file=tmp_path / "secrets" / "vault.key",
        recovery_key_file=tmp_path / "secrets" / "recovery.key",
        backup=tmp_path / "recovery" / "backup.hcb",
        restore_target=tmp_path / "restore",
    )
    counts = migration.migrate(args)
    assert counts["migrated_documents"] == 1
    assert (args.target / "index.json").read_bytes().startswith(MAGIC)
    assert (args.target / "documents" / "robert.bin").read_bytes().startswith(MAGIC)
    assert args.backup.is_file() and args.restore_target.is_dir()
