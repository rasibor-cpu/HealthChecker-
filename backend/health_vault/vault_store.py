"""Append-only Health Vault store (filesystem). Never overwrites document payloads."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.models import MedicalDocument, Measurement, utc_now


class VaultStore:
    """Permanent vault under vault_storage/ — originals + JSON indexes."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or Path(__file__).resolve().parents[2] / "vault_storage")
        self.documents_dir = self.root / "documents"
        self.index_path = self.root / "index.json"
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_index(self._empty())

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": "hc.health_vault.v1",
            "documents": [],
            "measurements": [],
            "audit": [],
            "imports": [],
            "import_log": [],
            "trends": {},
            "health_intelligence": {"observations": [], "disclaimer": ""},
            "encounters": [],
            "medications": [],
            "profile": {"diagnoses": [], "medications": []},
            "batch_audits": [],
            "ai_import_previews": {},
            "ai_import_audits": [],
            # HC-301 Guardian extensions (additive; never rewrite clinical docs)
            "alerts": [],
            "baselines": {},
            "cgm_sensors": [],
            "cgm_inventory": {},
            "cgm_continuity": {},
            "data_gaps": [],
            "timeline_events": [],
            "guardian_status": {},
            "guardian_audits": [],
            # HC-302 continuous monitoring extensions (additive)
            "observations": [],
            "connector_cursors": {},
            "connector_sync_health": {},
            "monitoring_status": {},
            "monitoring_audits": [],
            "monitoring_scheduler": {},
        }

    def _read_index(self) -> dict[str, Any]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty()

    def _write_index(self, data: dict[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def _audit(self, data: dict[str, Any], action: str, detail: dict[str, Any] | None = None) -> None:
        data.setdefault("audit", []).append(
            {
                "id": str(uuid4()),
                "at": utc_now(),
                "action": action,
                "detail": detail or {},
            }
        )

    @staticmethod
    def sha256_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def store(
        self,
        *,
        document: MedicalDocument,
        measurements: list[Measurement],
        content: bytes | None = None,
        interpretation: str | None = None,
        parser: dict[str, Any] | None = None,
        import_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read_index()
        if any(d.get("id") == document.id for d in data["documents"]):
            raise ValueError("Document id already exists — refuse overwrite")

        existing = None
        if document.sha256:
            existing = next(
                (d for d in data["documents"] if d.get("sha256") == document.sha256),
                None,
            )

        wrote_path: Path | None = None
        try:
            if content is not None and existing is None:
                dest = self.documents_dir / f"{document.id}.bin"
                if dest.exists():
                    raise ValueError("Storage path exists — refuse overwrite")
                dest.write_bytes(content)
                wrote_path = dest
                # Public URI is vault-relative — never require absolute filesystem exposure.
                document.storage_uri = f"vault://documents/{document.id}.bin"
                document.size_bytes = len(content)
            elif existing is not None:
                document.storage_uri = existing.get("storage_uri")
                document.duplicate_of = existing.get("id")
                tags = list(document.tags or [])
                if "duplicate_content" not in tags:
                    tags.append("duplicate_content")
                document.tags = tags

            if interpretation:
                document.interpretation = interpretation

            data["documents"].append(document.to_dict())
            for m in measurements:
                if not m.document_id:
                    m.document_id = document.id
                data["measurements"].append(m.to_dict())

            import_record = {
                "import_id": str(uuid4()),
                "document_id": document.id,
                "imported_at": document.imported_at,
                "parser": parser,
                "confidence": document.parser_confidence,
                "sha256": document.sha256,
                "measurement_count": len(measurements),
                "duplicate_content": existing is not None,
                "meta": import_meta or {},
            }
            data["imports"].append(import_record)
            self._audit(
                data,
                "document_imported",
                {
                    "document_id": document.id,
                    "sha256": document.sha256,
                    "parser": parser,
                    "duplicate_content": existing is not None,
                },
            )
            self._write_index(data)
        except Exception:
            if wrote_path is not None and wrote_path.exists():
                try:
                    wrote_path.unlink()
                except Exception:
                    pass
            raise
        return {"document": document.to_dict(), "import_record": import_record, "index": data}

    def list_documents(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("documents") or [])

    def list_measurements(self, **filters: Any) -> list[dict[str, Any]]:
        items = list(self._read_index().get("measurements") or [])
        if filters.get("document_id"):
            items = [m for m in items if m.get("document_id") == filters["document_id"]]
        if filters.get("metric"):
            items = [m for m in items if m.get("metric") == filters["metric"]]
        return items

    def save_trends(self, trends: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        data["trends"] = trends
        self._audit(data, "trends_updated", {"keys": list(trends.keys())})
        self._write_index(data)
        return trends

    def get_trends(self) -> dict[str, Any]:
        return dict(self._read_index().get("trends") or {})

    def update_profile(self, partial: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        profile = dict(data.get("profile") or {})
        profile.update(partial or {})
        data["profile"] = profile
        self._audit(data, "profile_updated", {"keys": list((partial or {}).keys())})
        self._write_index(data)
        return profile

    def get_profile(self) -> dict[str, Any]:
        return dict(self._read_index().get("profile") or {})

    def audit(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("audit") or [])

    def imports(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("imports") or [])

    def import_log(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("import_log") or [])

    def record_batch_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        """Append HC-201H batch confirmation metadata (no private payloads)."""
        data = self._read_index()
        entry = dict(audit or {})
        entry.setdefault("recorded_at", utc_now())
        data.setdefault("batch_audits", []).append(entry)
        self._audit(data, "batch_import_completed", {"batch_id": entry.get("batch_id")})
        self._write_index(data)
        return entry

    def list_batch_audits(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("batch_audits") or [])

    def save_ai_import_preview(self, ticket: dict[str, Any]) -> dict[str, Any]:
        """Persist short-lived AI import preview ticket (metadata only, no chat body)."""
        data = self._read_index()
        previews = dict(data.get("ai_import_previews") or {})
        pid = str(ticket.get("preview_id") or uuid4())
        entry = dict(ticket or {})
        entry["preview_id"] = pid
        entry.setdefault("created_at", utc_now())
        previews[pid] = entry
        data["ai_import_previews"] = previews
        self._audit(data, "ai_import_preview_created", {"preview_id": pid})
        self._write_index(data)
        return entry

    def get_ai_import_preview(self, preview_id: str | None) -> dict[str, Any] | None:
        if not preview_id:
            return None
        previews = self._read_index().get("ai_import_previews") or {}
        ticket = previews.get(str(preview_id))
        return dict(ticket) if ticket else None

    def consume_ai_import_preview(self, preview_id: str | None) -> None:
        if not preview_id:
            return
        data = self._read_index()
        previews = dict(data.get("ai_import_previews") or {})
        if str(preview_id) in previews:
            previews[str(preview_id)]["status"] = "consumed"
            previews[str(preview_id)]["consumed_at"] = utc_now()
            data["ai_import_previews"] = previews
            self._audit(data, "ai_import_preview_consumed", {"preview_id": str(preview_id)})
            self._write_index(data)

    def record_ai_import_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        """Append HC-202 AI import audit (no conversation text unless explicitly imported)."""
        data = self._read_index()
        entry = dict(audit or {})
        entry.setdefault("id", str(uuid4()))
        entry.setdefault("recorded_at", utc_now())
        conv = dict(entry.get("conversation") or {})
        if conv.get("conversation_text") and not conv.get("conversation_text_imported"):
            conv.pop("conversation_text", None)
        entry["conversation"] = conv
        data.setdefault("ai_import_audits", []).append(entry)
        self._audit(
            data,
            "ai_health_import_completed",
            {
                "batch_id": entry.get("batch_id"),
                "ai_provider": entry.get("ai_provider"),
                "imported_count": entry.get("imported_count"),
            },
        )
        self._write_index(data)
        return entry

    def list_ai_import_audits(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("ai_import_audits") or [])

    def health_intelligence(self) -> dict[str, Any]:
        return dict(self._read_index().get("health_intelligence") or {})

    def resolve_storage_path(self, storage_uri: str | None, document_id: str | None = None) -> Path | None:
        """Map public vault:// URI (or legacy absolute path) to local blob path."""
        if not storage_uri and document_id:
            candidate = self.documents_dir / f"{document_id}.bin"
            return candidate if candidate.exists() else None
        if not storage_uri:
            return None
        uri = str(storage_uri)
        if uri.startswith("vault://documents/"):
            name = uri.split("/")[-1]
            return self.documents_dir / name
        if uri.startswith("idb://"):
            return None
        p = Path(uri)
        return p if p.exists() else None

    def verify_integrity(self) -> dict[str, Any]:
        data = self._read_index()
        ids: set[str] = set()
        issues: list[str] = []
        for d in data.get("documents") or []:
            did = d.get("id")
            if did in ids:
                issues.append(f"duplicate_document_id:{did}")
            ids.add(did)
            uri = d.get("storage_uri")
            if d.get("duplicate_of"):
                continue
            if uri and str(uri).startswith("idb://"):
                continue
            path = self.resolve_storage_path(uri, did)
            if uri and path is None:
                issues.append(f"missing_blob:{did}")
            elif path is not None and not path.exists():
                issues.append(f"missing_blob:{did}")
        for m in data.get("measurements") or []:
            if m.get("document_id") and m["document_id"] not in ids:
                issues.append(f"orphan_measurement:{m.get('measurement_id')}")
        return {
            "ok": not issues,
            "issues": issues,
            "document_count": len(data.get("documents") or []),
        }

    def reset_for_tests(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self._write_index(self._empty())

    # --- HC-301 Guardian persistence (append/upsert; never destroys clinical docs) ---

    def list_alerts(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("alerts") or [])

    def upsert_alert(self, alert: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        alerts = list(data.get("alerts") or [])
        aid = str(alert.get("alert_id") or uuid4())
        alert = dict(alert)
        alert["alert_id"] = aid
        replaced = False
        for i, existing in enumerate(alerts):
            if existing.get("alert_id") == aid:
                alerts[i] = alert
                replaced = True
                break
        if not replaced:
            alerts.append(alert)
        data["alerts"] = alerts
        self._audit(data, "alert_upserted", {"alert_id": aid, "status": alert.get("status")})
        self._write_index(data)
        return alert

    def save_baselines(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        data["baselines"] = dict(payload or {})
        self._audit(data, "baselines_updated", {"patient_id": (payload or {}).get("patient_id")})
        self._write_index(data)
        return data["baselines"]

    def get_baselines(self) -> dict[str, Any]:
        return dict(self._read_index().get("baselines") or {})

    def list_cgm_sensors(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("cgm_sensors") or [])

    def upsert_cgm_sensor(self, sensor: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        sensors = list(data.get("cgm_sensors") or [])
        sid = str(sensor.get("sensor_id") or uuid4())
        sensor = dict(sensor)
        sensor["sensor_id"] = sid
        for i, existing in enumerate(sensors):
            if existing.get("sensor_id") == sid:
                sensors[i] = sensor
                data["cgm_sensors"] = sensors
                self._audit(data, "cgm_sensor_upserted", {"sensor_id": sid, "status": sensor.get("status")})
                self._write_index(data)
                return sensor
        sensors.append(sensor)
        data["cgm_sensors"] = sensors
        self._audit(data, "cgm_sensor_upserted", {"sensor_id": sid, "status": sensor.get("status")})
        self._write_index(data)
        return sensor

    def get_cgm_inventory(self, patient_id: str = "default-patient") -> dict[str, Any] | None:
        inv = self._read_index().get("cgm_inventory") or {}
        if isinstance(inv, dict) and inv.get("patient_id") == patient_id:
            return dict(inv)
        if isinstance(inv, dict) and patient_id in inv and isinstance(inv.get(patient_id), dict):
            return dict(inv[patient_id])
        return None

    def save_cgm_inventory(self, inventory: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        inv = dict(inventory or {})
        data["cgm_inventory"] = inv
        self._audit(data, "cgm_inventory_updated", {"patient_id": inv.get("patient_id")})
        self._write_index(data)
        return inv

    def save_cgm_continuity(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        data["cgm_continuity"] = dict(payload or {})
        self._audit(data, "cgm_continuity_updated", {"state": (payload or {}).get("state")})
        self._write_index(data)
        return data["cgm_continuity"]

    def get_cgm_continuity(self) -> dict[str, Any]:
        return dict(self._read_index().get("cgm_continuity") or {})

    def list_data_gaps(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("data_gaps") or [])

    def upsert_data_gap(self, gap: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        gaps = list(data.get("data_gaps") or [])
        gid = str(gap.get("gap_id") or uuid4())
        gap = dict(gap)
        gap["gap_id"] = gid
        for i, existing in enumerate(gaps):
            if existing.get("gap_id") == gid:
                gaps[i] = gap
                data["data_gaps"] = gaps
                self._audit(data, "data_gap_upserted", {"gap_id": gid})
                self._write_index(data)
                return gap
        gaps.append(gap)
        data["data_gaps"] = gaps
        self._audit(data, "data_gap_upserted", {"gap_id": gid})
        self._write_index(data)
        return gap

    def list_timeline_events(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("timeline_events") or [])

    def append_timeline_event(self, event: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        events = list(data.get("timeline_events") or [])
        entry = dict(event or {})
        entry.setdefault("event_id", str(uuid4()))
        dedupe = entry.get("dedupe_key")
        if dedupe and any(e.get("dedupe_key") == dedupe for e in events):
            return entry
        events.append(entry)
        data["timeline_events"] = events
        self._audit(data, "timeline_event_appended", {"kind": entry.get("kind")})
        self._write_index(data)
        return entry

    def save_guardian_status(self, status: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        data["guardian_status"] = dict(status or {})
        self._audit(data, "guardian_status_updated", {"overall_state": (status or {}).get("overall_state")})
        self._write_index(data)
        return data["guardian_status"]

    def get_guardian_status(self) -> dict[str, Any]:
        return dict(self._read_index().get("guardian_status") or {})

    def append_guardian_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        row = dict(entry or {})
        row.setdefault("id", str(uuid4()))
        data.setdefault("guardian_audits", []).append(row)
        self._audit(data, "guardian_audit_appended", {"audit_id": row.get("audit_id") or row.get("id")})
        self._write_index(data)
        return row

    def list_guardian_audits(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("guardian_audits") or [])

    # --- HC-302 continuous monitoring persistence ---

    def list_observations(self) -> list[dict[str, Any]]:
        return list(self._read_index().get("observations") or [])

    def get_observation_by_fingerprint(
        self,
        fingerprint: str,
        patient_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not fingerprint:
            return None
        for row in self.list_observations():
            if row.get("fingerprint") != fingerprint:
                continue
            if patient_id is not None and str(row.get("patient_id") or "default-patient") != patient_id:
                continue
            return dict(row)
        return None

    def upsert_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        rows = list(data.get("observations") or [])
        oid = str(observation.get("observation_id") or uuid4())
        row = dict(observation)
        row["observation_id"] = oid
        fp = row.get("fingerprint")
        for i, existing in enumerate(rows):
            if existing.get("observation_id") == oid or (
                fp and existing.get("fingerprint") == fp
            ):
                rows[i] = row
                data["observations"] = rows
                # Audit without clinical values
                self._audit(
                    data,
                    "observation_upserted",
                    {
                        "observation_id": oid,
                        "metric_type": row.get("metric_type"),
                        "acquisition_mode": row.get("acquisition_mode"),
                        "fingerprint": fp,
                    },
                )
                self._write_index(data)
                return row
        rows.append(row)
        data["observations"] = rows
        self._audit(
            data,
            "observation_upserted",
            {
                "observation_id": oid,
                "metric_type": row.get("metric_type"),
                "acquisition_mode": row.get("acquisition_mode"),
                "fingerprint": fp,
            },
        )
        self._write_index(data)
        return row

    def save_connector_cursor(self, connector_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        cursors = dict(data.get("connector_cursors") or {})
        key = str(connector_id)
        patient_id = str((payload or {}).get("patient_id") or "default-patient")
        # Nest by connector then patient for multi-patient safety
        bucket = dict(cursors.get(key) or {})
        if not isinstance(bucket, dict):
            bucket = {}
        bucket[patient_id] = dict(payload or {})
        cursors[key] = bucket
        data["connector_cursors"] = cursors
        self._audit(data, "connector_cursor_saved", {"connector_id": key, "patient_id": patient_id})
        self._write_index(data)
        return bucket[patient_id]

    def get_connector_cursor(
        self, connector_id: str, patient_id: str = "default-patient"
    ) -> dict[str, Any] | None:
        cursors = self._read_index().get("connector_cursors") or {}
        bucket = cursors.get(str(connector_id)) or {}
        if isinstance(bucket, dict) and patient_id in bucket:
            return dict(bucket[patient_id])
        # Legacy flat cursor shape
        if isinstance(bucket, dict) and bucket.get("cursor") is not None and bucket.get("patient_id") == patient_id:
            return dict(bucket)
        return None

    def save_connector_sync_health(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        health = dict(data.get("connector_sync_health") or {})
        cid = str((payload or {}).get("connector_id") or "unknown")
        patient_id = str((payload or {}).get("patient_id") or "default-patient")
        bucket = dict(health.get(cid) or {})
        bucket[patient_id] = dict(payload or {})
        health[cid] = bucket
        data["connector_sync_health"] = health
        self._audit(
            data,
            "connector_sync_health_saved",
            {"connector_id": cid, "patient_id": patient_id, "status": (payload or {}).get("status")},
        )
        self._write_index(data)
        return bucket[patient_id]

    def list_connector_sync_health(self) -> list[dict[str, Any]]:
        health = self._read_index().get("connector_sync_health") or {}
        rows: list[dict[str, Any]] = []
        if not isinstance(health, dict):
            return rows
        for cid, bucket in health.items():
            if isinstance(bucket, dict) and any(
                isinstance(v, dict) and ("status" in v or "cursor" in v or "updated_at" in v)
                for v in bucket.values()
            ):
                # patient-nested
                for pid, row in bucket.items():
                    if isinstance(row, dict):
                        item = dict(row)
                        item.setdefault("connector_id", cid)
                        item.setdefault("patient_id", pid)
                        rows.append(item)
            elif isinstance(bucket, dict):
                item = dict(bucket)
                item.setdefault("connector_id", cid)
                rows.append(item)
        return rows

    def save_monitoring_status(self, status: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        data["monitoring_status"] = dict(status or {})
        self._audit(
            data,
            "monitoring_status_updated",
            {"patient_id": (status or {}).get("patient_id")},
        )
        self._write_index(data)
        return data["monitoring_status"]

    def get_monitoring_status(self) -> dict[str, Any]:
        return dict(self._read_index().get("monitoring_status") or {})

    def append_monitoring_audit(self, entry: dict[str, Any]) -> dict[str, Any]:
        data = self._read_index()
        row = dict(entry or {})
        row.setdefault("id", str(uuid4()))
        data.setdefault("monitoring_audits", []).append(row)
        self._audit(data, "monitoring_audit_appended", {"audit_id": row.get("id")})
        self._write_index(data)
        return row

    def save_monitoring_scheduler_state(
        self, patient_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        data = self._read_index()
        bucket = dict(data.get("monitoring_scheduler") or {})
        pid = str(patient_id or "default-patient")
        row = dict(state or {})
        row["patient_id"] = pid
        bucket[pid] = row
        data["monitoring_scheduler"] = bucket
        self._audit(data, "monitoring_scheduler_updated", {"patient_id": pid, "status": row.get("status")})
        self._write_index(data)
        return row

    def get_monitoring_scheduler_state(self, patient_id: str = "default-patient") -> dict[str, Any]:
        bucket = self._read_index().get("monitoring_scheduler") or {}
        row = bucket.get(str(patient_id or "default-patient"))
        return dict(row) if isinstance(row, dict) else {}
