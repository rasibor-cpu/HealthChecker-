"""Live end-to-end acceptance test for HC-313B."""

import logging
from pathlib import Path
import sys

from backend.health_vault.acquisition.gmail_acquirer import GmailAcquirer
from backend.health_vault.acquisition.gmail_api_connector import GmailApiConnector
from backend.health_vault.acquisition.gmail_config import GmailAcquisitionConfig
from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.acquisition.patient_identity import PatientIdentityVerifier
from backend.health_vault.vault_store import VaultStore
from backend.health_vault.intake.runner import IntakeRunner
from backend.health_vault.intake.intake_config import get_default_intake_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main():
    # 1. Setup Gmail Acquirer
    token_path = Path(r"C:\ProgramData\HealthChecker\config\gmail_token.json")
    if not token_path.exists():
        print("Token not found!")
        sys.exit(1)
        
    vault = VaultStore()
    profile = vault.get_profile()
    name = profile.get('name') if profile else None
    if not name:
        print("Profile name missing in vault. Updating vault profile to 'Robert Asibor' for testing...")
        profile = {"name": "Robert Asibor", "dob": "1980-01-01"}
        vault.update_profile(profile)
    else:
        print(f"Registered User Name: {name}")
    
    connector = GmailApiConnector(token_path)
    state_store = AcquisitionStateStore(Path(r"C:\ProgramData\HealthChecker\config\acquisition_state.json"))
    verifier = PatientIdentityVerifier(profile)
    config = GmailAcquisitionConfig()
    
    acquirer = GmailAcquirer(
        connector=connector,
        verifier=verifier,
        config=config
    )
    
    print("\n--- RUNNING GMAIL ACQUISITION ---")
    acquirer.run_scan()
    
    # 2. Setup HC-312 Automatic Intake Runner
    print("\n--- RUNNING HC-312 AUTOMATIC INTAKE ---")
    intake_config = get_default_intake_config()
    runner = IntakeRunner(config=intake_config)
    summary = runner.run()
    
    print("\n--- INTAKE SUMMARY ---")
    print(f"Scanned: {summary.get('scanned', 0)}")
    print(f"Completed: {summary.get('completed', 0)}")
    print(f"Quarantined: {summary.get('quarantine', 0)}")
    print(f"Pre-rejected: {summary.get('pre_rejected', 0)}")
    
    print("\n--- VAULT DOCUMENTS ---")
    docs = vault.list_documents()
    for doc in docs[-5:]:  # show latest 5
        print(f"Doc ID: {doc.id}, Provenance: {doc.provenance}")

if __name__ == "__main__":
    main()
