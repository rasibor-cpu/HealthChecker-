"""Tests for HC-313B Production Gmail API Connector."""

import base64
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.health_vault.acquisition.gmail_acquirer import GmailConnectorProtocol
from backend.health_vault.acquisition.gmail_api_connector import GmailApiConnector
from backend.health_vault.acquisition.gmail_models import (
    GmailAttachmentRetrievalError,
    GmailConnectorError,
)

class TestProductionGmailConnector(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(os.environ.get("TEMP", "/tmp")) / "hc313b_test_tokens"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = self.test_dir / "test_token.json"
        if self.token_path.exists():
            self.token_path.unlink()
            
    def tearDown(self):
        if self.token_path.exists():
            self.token_path.unlink()
        try:
            self.test_dir.rmdir()
        except OSError:
            pass

    def test_a_satisfies_protocol(self):
        """Production connector satisfies GmailConnectorProtocol."""
        connector = GmailApiConnector(self.token_path)
        self.assertIsInstance(connector, GmailConnectorProtocol)

    def test_b_read_only_scope(self):
        """Connector requests only gmail.readonly scope."""
        from backend.health_vault.acquisition.gmail_api_connector import SCOPES
        self.assertEqual(SCOPES, ["https://www.googleapis.com/auth/gmail.readonly"])

    @patch("backend.health_vault.acquisition.gmail_api_connector.build")
    @patch("backend.health_vault.acquisition.gmail_api_connector.Credentials")
    def test_c_successful_message_discovery(self, mock_creds, mock_build):
        """Connector retrieves message metadata."""
        # Create a valid token
        self.token_path.write_text('{"token": "fake"}')
        mock_creds.from_authorized_user_file.return_value = MagicMock(valid=True)
        
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        # Mock messages().list()
        mock_list = MagicMock()
        mock_list.execute.return_value = {"messages": [{"id": "msg1"}]}
        mock_service.users().messages().list.return_value = mock_list
        
        # Mock messages().get() for metadata
        mock_get = MagicMock()
        mock_get.execute.return_value = {
            "id": "msg1",
            "threadId": "thread1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "doctor@clinic.com"},
                    {"name": "To", "value": "patient@example.com"},
                    {"name": "Subject", "value": "Lab Results"},
                ]
            }
        }
        mock_service.users().messages().get.return_value = mock_get
        
        connector = GmailApiConnector(self.token_path)
        messages = connector.list_messages()
        
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].message_id, "msg1")
        self.assertEqual(messages[0].sender, "doctor@clinic.com")
        self.assertEqual(messages[0].subject, "Lab Results")

    @patch("backend.health_vault.acquisition.gmail_api_connector.build")
    @patch("backend.health_vault.acquisition.gmail_api_connector.Credentials")
    def test_d_attachment_retrieval(self, mock_creds, mock_build):
        """Connector retrieves attachment bytes correctly."""
        self.token_path.write_text('{"token": "fake"}')
        mock_creds.from_authorized_user_file.return_value = MagicMock(valid=True)
        
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        mock_get = MagicMock()
        # base64 urlsafe
        fake_data = base64.urlsafe_b64encode(b"PDF_CONTENT").decode("utf-8")
        mock_get.execute.return_value = {"data": fake_data}
        mock_service.users().messages().attachments().get.return_value = mock_get
        
        connector = GmailApiConnector(self.token_path)
        data = connector.get_attachment_bytes("msg1", "att1")
        self.assertEqual(data, b"PDF_CONTENT")

    @patch("backend.health_vault.acquisition.gmail_api_connector.build")
    @patch("backend.health_vault.acquisition.gmail_api_connector.Credentials")
    def test_h_transient_failure_raises_connector_error(self, mock_creds, mock_build):
        """Transient Gmail API failures are wrapped in standard errors."""
        self.token_path.write_text('{"token": "fake"}')
        mock_creds.from_authorized_user_file.return_value = MagicMock(valid=True)
        
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.users().messages().list.side_effect = Exception("API Down")
        
        connector = GmailApiConnector(self.token_path)
        with self.assertRaises(GmailConnectorError):
            connector.list_messages()

    def test_u_no_hardcoded_identities(self):
        """Ensure no 'Robert' or 'Asibor' in connector source."""
        source_path = Path(__file__).parent.parent / "backend/health_vault/acquisition/gmail_api_connector.py"
        content = source_path.read_text(encoding="utf-8")
        self.assertNotIn("Robert", content)
        self.assertNotIn("Asibor", content)

    def test_s_no_vault_store_calls(self):
        """Connector does not bypass architecture."""
        source_path = Path(__file__).parent.parent / "backend/health_vault/acquisition/gmail_api_connector.py"
        content = source_path.read_text(encoding="utf-8")
        self.assertNotIn("VaultStore", content)
        self.assertNotIn("ImportPipeline", content)

    @patch("backend.health_vault.acquisition.gmail_api_connector.build")
    @patch("backend.health_vault.acquisition.gmail_api_connector.Credentials")
    def test_w_atomic_token_refresh(self, mock_creds, mock_build):
        """If token is expired but has refresh token, it is refreshed and saved atomically."""
        self.token_path.write_text('{"token": "expired"}')
        
        mock_cred_instance = MagicMock()
        mock_cred_instance.valid = False
        mock_cred_instance.expired = True
        mock_cred_instance.refresh_token = "some_refresh"
        mock_cred_instance.to_json.return_value = '{"token": "refreshed"}'
        mock_creds.from_authorized_user_file.return_value = mock_cred_instance
        
        connector = GmailApiConnector(self.token_path)
        # Should trigger refresh internally
        connector._get_service()
        
        # Verify it was saved
        new_token = self.token_path.read_text(encoding="utf-8")
        self.assertEqual(new_token, '{"token": "refreshed"}')
        
    def test_v_no_embedded_secrets(self):
        """Ensure no hard-coded secrets or client IDs in source."""
        source_path = Path(__file__).parent.parent / "backend/health_vault/acquisition/gmail_api_connector.py"
        content = source_path.read_text(encoding="utf-8").lower()
        for secret in ["client_secret", "client_id", "ya29.", "1//0"]:
            self.assertNotIn(secret, content)

    def test_requires_user_auth_if_no_token(self):
        """If no token exists, raises clear error indicating auth required."""
        connector = GmailApiConnector(self.token_path)
        with self.assertRaisesRegex(GmailConnectorError, "User authorization required"):
            connector.list_messages()
