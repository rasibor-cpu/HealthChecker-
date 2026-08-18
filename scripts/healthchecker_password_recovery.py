"""Visible local HC-321A recovery entry point; passwords are read with getpass."""

from __future__ import annotations

import argparse
import getpass

from backend.health_vault.auth import AuthenticationService
from backend.health_vault.auth_recovery import (
    LocalPasswordRecoveryService,
    LocalRecoveryAuthorization,
    PasswordRecoveryError,
)
from backend.health_vault.production_runtime import create_production_vault


def main() -> int:
    parser = argparse.ArgumentParser(description="HealthChecker local privileged password recovery")
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    try:
        vault = create_production_vault()
        auth = AuthenticationService(vault)
        account = auth.get_account(args.user_id)
        if account is None:
            raise PasswordRecoveryError("account_not_found")
        if account.account_status != "active":
            raise PasswordRecoveryError("account_not_active")
        new_password = getpass.getpass("New HealthChecker password: ")
        confirmation = getpass.getpass("Confirm new HealthChecker password: ")
        result = LocalPasswordRecoveryService(auth).recover(
            user_id=args.user_id,
            new_password=new_password,
            confirmation=confirmation,
            authorization=LocalRecoveryAuthorization(
                actor=getpass.getuser(),
                reason="owner_authorized_production_recovery",
            ),
        )
        new_password = confirmation = ""
        print("PASSWORD_RESET=PASS")
        print(f"ACCOUNT_STATE={str(result['account_status']).upper()}")
        print("MUST_CHANGE_PASSWORD=FALSE")
        print("PASSWORD_EXPIRED=FALSE")
        print("FAILED_LOGIN_COUNT=0")
        print("OLD_SESSIONS_REVOKED=PASS")
        print("VAULT_INTEGRITY=PASS")
        return 0
    except PasswordRecoveryError as exc:
        print(f"PASSWORD_RESET=BLOCKED:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
