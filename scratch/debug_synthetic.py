import logging
from pathlib import Path
import sys

from backend.health_vault.acquisition.gmail_acquirer import GmailAcquirer, _extract_patient_fields_from_text
from backend.health_vault.acquisition.gmail_api_connector import GmailApiConnector
from backend.health_vault.acquisition.gmail_config import GmailAcquisitionConfig
from backend.health_vault.acquisition.patient_identity import PatientIdentityVerifier
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.acquisition.gmail_models import GmailMessage

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

def main():
    token_path = Path(r"C:\ProgramData\HealthChecker\config\gmail_token.json")
    vault = VaultStore()
    profile = vault.get_profile()
    
    connector = GmailApiConnector(token_path)
    verifier = PatientIdentityVerifier(profile)
    config = GmailAcquisitionConfig()
    
    # Just to get the attachment bytes
    messages = connector.list_messages()
    msg = next((m for m in messages if m.message_id == '1a0083d321820cd3'), None)
    if not msg:
        print("Message not found")
        return
        
    attachments = connector.list_attachments(msg)
    for att in attachments:
        if att.filename == 'HC313B_SYNTHETIC_LAB_REPORT.pdf':
            content = connector.get_attachment_bytes(msg.message_id, att.attachment_id)
            print("Attachment fetched. Length:", len(content))
            
            # Now let's see what the Acquirer does. We bypass state by just running the classification logic manually or creating a fresh Acquirer
            acquirer = GmailAcquirer(connector=connector, verifier=verifier, config=config)
            from backend.health_vault.acquisition.gmail_models import GmailAttachment
            
            stub = GmailAttachment(
                attachment_id=att.attachment_id,
                filename=att.filename,
                mime_type=att.mime_type,
                size_bytes=att.size_bytes,
                content=b"",
                sha256=""
            )
            
            # The acquirer handles text extraction, classification and identity verification
            # But the logic is spread out. Let's just run Acquirer logic manually for debugging.
            with open(r"c:\rasib\source\HealthChecker-HC310E\scratch\test.pdf", "wb") as f:
                f.write(content)
            print("Wrote to test.pdf")
            return

if __name__ == "__main__":
    main()
