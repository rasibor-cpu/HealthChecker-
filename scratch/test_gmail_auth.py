import sys
from pathlib import Path
from backend.health_vault.acquisition.gmail_api_connector import GmailApiConnector

def main():
    token_path = Path(r"C:\ProgramData\HealthChecker\config\gmail_token.json")
    if not token_path.exists():
        print("Token not found!")
        sys.exit(1)
        
    try:
        connector = GmailApiConnector(token_path)
        print("GmailApiConnector instantiated successfully.")
        
        messages = connector.list_messages(label_filter="inbox")
        print(f"Authentication successful! Found {len(messages)} candidate messages with attachments in inbox.")
        
    except Exception as exc:
        print(f"Authentication or API error: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    main()
