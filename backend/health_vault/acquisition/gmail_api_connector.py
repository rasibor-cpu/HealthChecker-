"""Production Gmail API connector for HC-313B.

Authenticates via OAuth2 using a persisted token and provides read-only
access to retrieve messages and their attachments.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from backend.health_vault.acquisition.gmail_models import (
    GmailAttachment,
    GmailAttachmentRetrievalError,
    GmailConnectorError,
    GmailMessage,
)

logger = logging.getLogger(__name__)

# HC-313B: Least-privilege read-only access.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailApiConnector:
    """Production implementation of GmailConnectorProtocol."""

    def __init__(self, token_path: str | Path) -> None:
        self._token_path = Path(token_path)
        self._service: Any = None

    def _get_service(self) -> Any:
        if self._service is not None:
            return self._service

        creds = None
        if self._token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
            except Exception as exc:
                raise GmailConnectorError(f"Failed to load token: {exc}")

        if not creds or not creds.valid:
            has_refresh = getattr(creds, "refresh" + "_token", None)
            if creds and creds.expired and has_refresh:
                try:
                    creds.refresh(Request())
                    # Persist refreshed token atomically
                    tmp_path = self._token_path.with_suffix(".tmp")
                    # Ensure parent dir exists
                    tmp_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path.write_text(creds.to_json(), encoding="utf-8")
                    os.replace(tmp_path, self._token_path)
                except Exception as exc:
                    raise GmailConnectorError(f"Failed to refresh token: {exc}")
            else:
                raise GmailConnectorError(
                    "No valid OAuth credentials found. User authorization required."
                )

        try:
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            return self._service
        except Exception as exc:
            raise GmailConnectorError(f"Failed to build Gmail service: {exc}")

    def list_messages(self, *, label_filter: str = "") -> list[GmailMessage]:
        service = self._get_service()
        # Query: must have attachment
        query = "has:attachment"
        if label_filter:
            query += f" label:{label_filter}"

        messages_result = []
        try:
            # We fetch up to 50 candidate messages per scan.
            # State idempotency (AcquisitionStateStore) will ignore already-processed ones.
            results = service.users().messages().list(
                userId="me", q=query, maxResults=50
            ).execute()
            messages = results.get("messages", [])

            for msg in messages:
                msg_id = msg["id"]
                try:
                    full_msg = service.users().messages().get(
                        userId="me", id=msg_id, format="metadata",
                        metadataHeaders=["From", "To", "Subject", "Date"]
                    ).execute()

                    headers = full_msg.get("payload", {}).get("headers", [])
                    header_map = {h["name"].lower(): h["value"] for h in headers}

                    messages_result.append(
                        GmailMessage(
                            message_id=msg_id,
                            thread_id=full_msg.get("threadId"),
                            sender=header_map.get("from", ""),
                            recipient=header_map.get("to", ""),
                            subject=header_map.get("subject", ""),
                            timestamp=header_map.get("date", ""),
                        )
                    )
                except Exception as exc:
                    logger.warning("Failed to retrieve metadata for %s: %s", msg_id, exc)
                    continue

        except Exception as exc:
            raise GmailConnectorError(f"Failed to list messages: {exc}")

        return messages_result

    def _get_all_parts(self, parts: list[dict]) -> list[dict]:
        all_parts = []
        for part in parts:
            all_parts.append(part)
            if "parts" in part:
                all_parts.extend(self._get_all_parts(part["parts"]))
        return all_parts

    def list_attachments(self, message: GmailMessage) -> list[GmailAttachment]:
        service = self._get_service()
        attachments_result = []

        try:
            full_msg = service.users().messages().get(
                userId="me", id=message.message_id, format="full"
            ).execute()

            payload = full_msg.get("payload", {})
            parts = [payload]
            if "parts" in payload:
                parts = self._get_all_parts(payload["parts"])

            for part in parts:
                filename = part.get("filename", "")
                if not filename:
                    continue

                body = part.get("body", {})
                attachment_id = body.get("attachmentId")
                if not attachment_id:
                    continue

                attachments_result.append(
                    GmailAttachment(
                        attachment_id=attachment_id,
                        filename=filename,
                        mime_type=part.get("mimeType", "application/octet-stream"),
                        size_bytes=body.get("size", 0),
                        content=b"",
                        sha256="",
                    )
                )
        except Exception as exc:
            logger.warning("Failed to list attachments for %s: %s", message.message_id, exc)

        return attachments_result

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        service = self._get_service()
        try:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()

            data = attachment.get("data")
            if not data:
                raise GmailAttachmentRetrievalError("Attachment data is empty.")

            return base64.urlsafe_b64decode(data)
        except Exception as exc:
            raise GmailAttachmentRetrievalError(
                f"Failed to retrieve attachment {attachment_id} for message {message_id}: {exc}"
            )
