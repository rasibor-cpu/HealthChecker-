"""HC321-C-C privacy notice, consent, and patient data-rights workflows.

Product controls only — not a HIPAA/SOC2/ISO/PIPEDA certification claim.
Legal/business policy owners fill notice text and retention decisions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.health_vault.models import utc_now
from backend.health_vault.vault_crypto import decrypt_bytes, encrypt_bytes

PRIVACY_NOTICE_VERSION = "hc.privacy_notice.v1.PLACEHOLDER"
PRIVACY_NOTICE_OWNER = "LEGAL_POLICY_OWNER_PLACEHOLDER"
CONSENT_PURPOSES = frozenset(
    {
        "product_use",
        "health_connect_sync",
        "local_analytics_quality",
    }
)
_CONSENT_CONTEXT = b"healthchecker/consent_registry/v1"
AMENDABLE_PROFILE_KEYS = frozenset(
    {
        "display_name",
        "name",
        "diagnoses",
        "medications",
        "notes",
        "date_of_birth",
    }
)


class PrivacyRightsError(ValueError):
    def __init__(self, code: str, status_code: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class PrivacyDataRightsService:
    """Consent + export + amendment + deliberate deletion for a patient vault."""

    def __init__(self, vault) -> None:
        self.vault = vault
        self.path = Path(vault.root) / "privacy_consent.json"
        self._key = getattr(vault, "_encryption_key", None)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": "hc.privacy_consent.v1",
                "privacy_notice_version": PRIVACY_NOTICE_VERSION,
                "privacy_notice_owner": PRIVACY_NOTICE_OWNER,
                "consents_by_user": {},
                "deletion_requests": {},
                "audit": [],
            }
        raw = self.path.read_bytes()
        if self._key is not None:
            raw = decrypt_bytes(raw, key=self._key, context=_CONSENT_CONTEXT)
        return json.loads(raw.decode("utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        raw = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        if self._key is not None:
            raw = encrypt_bytes(raw, key=self._key, context=_CONSENT_CONTEXT)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, self.path)

    def _audit(self, data: dict[str, Any], action: str, user_id: str, detail: dict[str, Any] | None = None) -> None:
        entry = {
            "event_id": str(uuid4()),
            "at": utc_now(),
            "action": action,
            "user_id": user_id,
            "outcome": "success",
        }
        if detail:
            # Never embed clinical payload values in privacy audit.
            entry["detail"] = {k: detail[k] for k in detail if k not in {"payload", "content", "measurements"}}
        data.setdefault("audit", []).append(entry)

    def privacy_notice(self) -> dict[str, Any]:
        return {
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "privacy_notice_owner": PRIVACY_NOTICE_OWNER,
            "certification_claims": [],
            "note": "Product control placeholders only; not a legal certification.",
            "purposes": sorted(CONSENT_PURPOSES),
        }

    def get_consent(self, user_id: str) -> dict[str, Any]:
        data = self._read()
        row = dict((data.get("consents_by_user") or {}).get(str(user_id)) or {})
        return {
            "user_id": str(user_id),
            "privacy_notice_version": data.get("privacy_notice_version") or PRIVACY_NOTICE_VERSION,
            "records": list(row.get("records") or []),
            "active": {
                purpose: any(
                    r.get("purpose") == purpose and r.get("state") == "granted" and not r.get("withdrawn_at")
                    for r in (row.get("records") or [])
                )
                for purpose in CONSENT_PURPOSES
            },
        }

    def record_consent(
        self,
        user_id: str,
        *,
        purpose: str,
        notice_version: str | None = None,
        provenance: str = "authenticated_user",
    ) -> dict[str, Any]:
        purpose_norm = str(purpose or "").strip()
        if purpose_norm not in CONSENT_PURPOSES:
            raise PrivacyRightsError("invalid_consent_purpose")
        data = self._read()
        by_user = data.setdefault("consents_by_user", {})
        row = by_user.setdefault(str(user_id), {"records": []})
        record = {
            "consent_id": str(uuid4()),
            "purpose": purpose_norm,
            "state": "granted",
            "granted_at": utc_now(),
            "withdrawn_at": None,
            "privacy_notice_version": notice_version or PRIVACY_NOTICE_VERSION,
            "provenance": provenance,
        }
        row["records"].append(record)
        self._audit(data, "consent_granted", str(user_id), {"purpose": purpose_norm})
        self._write(data)
        return record

    def withdraw_consent(self, user_id: str, *, purpose: str) -> dict[str, Any]:
        purpose_norm = str(purpose or "").strip()
        if purpose_norm not in CONSENT_PURPOSES:
            raise PrivacyRightsError("invalid_consent_purpose")
        data = self._read()
        row = (data.get("consents_by_user") or {}).get(str(user_id)) or {"records": []}
        updated = None
        for record in reversed(list(row.get("records") or [])):
            if record.get("purpose") == purpose_norm and record.get("state") == "granted" and not record.get("withdrawn_at"):
                record["state"] = "withdrawn"
                record["withdrawn_at"] = utc_now()
                updated = record
                break
        if updated is None:
            raise PrivacyRightsError("consent_not_found", 404)
        self._audit(data, "consent_withdrawn", str(user_id), {"purpose": purpose_norm})
        self._write(data)
        return updated

    def export_patient_package(self, user_id: str) -> dict[str, Any]:
        """Patient-scoped export; never includes other users' documents."""
        pid = str(user_id)
        docs = [d for d in self.vault.list_documents() if str(d.get("patient_id") or "") == pid]
        profile = self.vault.get_profile(pid)
        trends = self.vault.get_trends(pid)
        consent = self.get_consent(pid)
        package = {
            "schema_version": "hc.data_export.v1",
            "exported_at": utc_now(),
            "patient_id": pid,
            "profile": profile,
            "documents": docs,
            "document_count": len(docs),
            "trends_keys": sorted(trends.keys()),
            "consent": consent,
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
        }
        data = self._read()
        self._audit(data, "data_exported", pid, {"document_count": len(docs)})
        self._write(data)
        return package

    def amend_profile(self, user_id: str, amendments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(amendments, dict) or not amendments:
            raise PrivacyRightsError("amendment_required")
        unknown = set(amendments) - AMENDABLE_PROFILE_KEYS
        if unknown:
            raise PrivacyRightsError("amendment_keys_not_allowed")
        updated = self.vault.update_profile(amendments, patient_id=str(user_id))
        data = self._read()
        self._audit(data, "profile_amended", str(user_id), {"keys": sorted(amendments.keys())})
        self._write(data)
        return updated

    def request_deletion(self, user_id: str, *, confirmation: str) -> dict[str, Any]:
        """Two-step delete: request token only; does not destroy data yet."""
        if confirmation != "DELETE":
            raise PrivacyRightsError("deletion_confirmation_required")
        data = self._read()
        token = secrets_token()
        data.setdefault("deletion_requests", {})[str(user_id)] = {
            "token": token,
            "requested_at": utc_now(),
            "confirmed_at": None,
        }
        self._audit(data, "deletion_requested", str(user_id))
        self._write(data)
        return {"ok": True, "user_id": str(user_id), "confirmation_token": token, "status": "pending_confirmation"}

    def confirm_deletion(self, user_id: str, *, confirmation_token: str, confirmation: str) -> dict[str, Any]:
        """Deliberate authenticated deletion of patient-scoped clinical index entries."""
        if confirmation != "DELETE":
            raise PrivacyRightsError("deletion_confirmation_required")
        data = self._read()
        pending = (data.get("deletion_requests") or {}).get(str(user_id))
        if not pending or pending.get("token") != confirmation_token or pending.get("confirmed_at"):
            raise PrivacyRightsError("deletion_token_invalid", 403)
        pid = str(user_id)
        index = self.vault._read_index()
        before_docs = list(index.get("documents") or [])
        removed_ids = {
            str(d.get("id") or d.get("document_id") or "")
            for d in before_docs
            if str(d.get("patient_id") or "") == pid
        }
        remaining_docs = [d for d in before_docs if str(d.get("patient_id") or "") != pid]
        removed = len(before_docs) - len(remaining_docs)
        index["documents"] = remaining_docs
        measurements = list(index.get("measurements") or [])
        index["measurements"] = [
            m for m in measurements if str(m.get("document_id") or "") not in removed_ids
        ]
        trends = index.get("trends")
        if isinstance(trends, dict) and pid in trends:
            del trends[pid]
        observations = index.get("observations")
        if isinstance(observations, list):
            index["observations"] = [
                o for o in observations if str(o.get("patient_id") or "") != pid
            ]
        profiles = index.get("profiles_by_user_id")
        if isinstance(profiles, dict) and pid in profiles:
            profiles[pid] = {
                "diagnoses": [],
                "medications": [],
                "deletion_marker": True,
                "deleted_at": utc_now(),
            }
        self.vault._audit(index, "patient_data_deleted", {"patient_id": pid, "documents_removed": removed})
        self.vault._write_index(index)
        pending["confirmed_at"] = utc_now()
        self._audit(data, "deletion_confirmed", pid, {"documents_removed": removed})
        self._write(data)
        return {"ok": True, "user_id": pid, "documents_removed": removed, "status": "deleted"}


def secrets_token() -> str:
    from secrets import token_urlsafe

    return token_urlsafe(24)
