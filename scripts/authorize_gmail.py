"""Utility script to perform initial Gmail OAuth consent flow.

Run this script manually on the console to authorize HealthChecker
to read Gmail attachments. It will open a browser for consent.
"""

import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def main():
    if len(sys.argv) < 3:
        print("Usage: python authorize_gmail.py <path_to_client_secret.json> <output_token.json>")
        sys.exit(1)
        
    client_secrets_file = Path(sys.argv[1])
    token_file = Path(sys.argv[2])
    
    if not client_secrets_file.exists():
        print(f"Error: {client_secrets_file} does not exist.")
        sys.exit(1)
        
    print(f"Authorizing using {client_secrets_file}...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets_file), SCOPES
    )
    
    # Run local server to catch the callback
    creds = flow.run_local_server(port=0)
    
    # Save the credentials
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    
    print(f"\nAuthorization successful! Token saved to: {token_file}")
    print("Keep this token secure and ensure HealthChecker is configured to read from it.")

if __name__ == "__main__":
    main()
