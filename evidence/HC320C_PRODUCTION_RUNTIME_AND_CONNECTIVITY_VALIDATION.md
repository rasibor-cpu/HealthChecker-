# HC-320C Production Runtime and Connectivity Validation

Date: 2026-08-17
Branch: `hc311-encrypted-vault-at-rest`
Baseline: `fa6438373c18fc165270adb24bb796d2ff3dee77`

## Result

HC-320C pre-activation engineering is ready for the approved Cloudflare Tunnel
topology. Production activation and real-device acceptance remain pending; no
HTTP downgrade, invented tunnel identity, token, credential, certificate, or
private key was used to manufacture a pass.

## Baseline and topology

- Repository, branch, baseline commit, origin, and zero divergence: PASS.
- HC-320D commit `fa6438373c18fc165270adb24bb796d2ff3dee77` was
  pushed unchanged to the authoritative origin branch.
- Public DNS delegation check through resolver `1.1.1.1`: PASS; authoritative
  nameservers are `alla.ns.cloudflare.com` and `mike.ns.cloudflare.com`.
- `health.capitalstratasystems.com` is not published yet; tunnel creation and
  DNS routing remain pending.
- Pre-existing HC-314 runtime output remained outside the change set.
- Desktop browser runtime remains loopback-only through
  `scripts/start_healthchecker.ps1`.
- Mobile production origin is `https://health.capitalstratasystems.com` at the
  Cloudflare edge. The tunnel connects outbound to a loopback-only HealthChecker
  listener at `http://127.0.0.1:8766`; no router forwarding or LAN listener is
  used.
- Android retains the HC-319C/D API-only consumer boundary, native Health
  Connect integration, and WorkManager background synchronization.
- CSS and HealthChecker have separate service identity, endpoint configuration,
  encrypted preferences, worker name, retry state, listener, and lock state.

## Implemented controls

- Explicit production service identity `healthchecker.consumer.api`.
- HTTPS-only public origin; no silent HTTP fallback.
- The local production listener requires exactly `127.0.0.1`; unrestricted and
  LAN binding fail closed.
- Cloudflare edge TLS and the Android system trust store validate the public
  hostname. The local origin does not hold a public TLS private key.
- Tunnel configuration requires an externally created UUID and credentials
  file under ProgramData; neither is generated or committed.
- The ingress allowlist contains only the HealthChecker hostname and terminates
  with `http_status:404`.
- HealthChecker uses port 8766 and dedicated runtime/config/PID namespaces;
  CSS port 8765 and CSS state are never reused.
- Fixed-port collision and duplicate live-instance detection.
- Safe stale-PID recovery, bounded restart/backoff, clean PID removal, and
  privacy-safe heartbeat state.
- Runtime logs record only event, exit code, and attempt count; no origin,
  certificate path, credentials, token, patient identity, or PHI.
- Android TLS errors are cancelled, mixed content is forbidden, exact-origin
  navigation remains enforced, and cleartext remains debug/local-only.
- Retry UI, resume retry, WorkManager exponential backoff, and user-scoped queue
  deletion on logout/revocation remain intact.

## Validation

- Updated HC-319C/D, HC-320B/C focused band: **47 passed**.
- Android `testDebugUnitTest lintDebug`: **BUILD SUCCESSFUL**.
- Full Python regression: **1222 passed, 3 skipped, 5 subtests passed** in
  428.06 seconds.
- `git diff --check`: PASS; no commit authorized while the activation dependency
  remains unresolved.
- Connected device: `R3CX305A7DV`, model `SM_S928W`, authorized state `device`.

## Real-device gate

The S24 Ultra is available and Cloudflare activation succeeded, but real-device
login was stopped before credential entry after the stale plaintext runtime was
identified. Prior application navigation, Health Connect, reconnect interruption,
and concurrent CSS operation were previously validated for HC-319D's isolated
ADB development transport, but that result is not substituted for production
TLS acceptance.

Required recovery inputs:

1. Identify an existing protected key/verified encrypted backup, or authorize a
   controlled new-key migration from the preserved stale vault.
2. Validate the migrated/restored vault using HC-311 encryption and HC-318
   ownership/isolation checks in an isolated location.
3. Install the protected key at the production path and prove two clean
   HC-320B factory restarts on `127.0.0.1:8766`.
4. Re-enable the already configured tunnel service and complete real-device
   login, navigation, Health Connect, reconnect, and session acceptance.

After provisioning, repeat device login/session/navigation, Health Connect
delivery, network interruption/recovery, token revocation, password expiry, and
both CSS/HealthChecker startup orders before committing HC-320C.

## Gate report

`PRODUCTION_TLS=PASS` (fail-closed implementation and automated validation)

`HOST_IDENTITY_VALIDATION=PASS`

`DESKTOP_MOBILE_CONNECTIVITY=FAIL` (production endpoint not provisioned)

`MOBILE_RECONNECT=FAIL` (production real-device interruption run pending)

`DESKTOP_RUNTIME_RECOVERY=PASS`

`WATCHDOG_MONITORING=PASS`

`CSS_HEALTHCHECKER_CONCURRENT_ISOLATION=PASS` (namespaces plus prior HC-319D
real-device concurrency evidence; production TLS recheck pending with endpoint)

`REGRESSION=PASS`

`RESULT=HC320C_PRODUCTION_RUNTIME_AND_CONNECTIVITY_BLOCKED`

## Final recovery, activation, and real-device acceptance

The authorized Option 1 ownership disposition resolved the classification gate.
The controlled migration retained the verified plaintext recovery source without
modification, migrated only the single explicitly Robert-owned document into the
protected production profile, excluded four identified synthetic/test documents,
and quarantined the ambiguous document and insufficiently scoped linked material.
No quarantined or excluded material is visible as production clinical data.

The production vault was created through the HC-320B protected-key mechanism and
validated as HCVE encrypted at rest. There is no plaintext production index. The
encrypted vault, account registry, owner scoping, backup/restore path, and factory
readability all passed a complete process termination and clean production restart.
Keys, recovery copies, quarantine content, credentials, and clinical values remain
outside this repository and are not recorded here.

The dedicated HealthChecker tunnel was re-enabled only after the clean-restart
gate. `https://health.capitalstratasystems.com/mobile` returns HTTP 200 with a
system-trusted certificate and maps to the loopback-only origin at
`127.0.0.1:8766`. No router port forwarding is used. HealthChecker continues to
reject CSS port 8765 in its production launcher, uses separate runtime/tunnel
state, and did not alter the previously validated independent CSS configuration.

### S24 launcher and pairing remediation

- Authorized device: Samsung SM-S928W, serial `R3CX305A7DV`.
- The installed package was found to be legacy version `hc303b.1.0.0` (version
  code 1), which did not contain the HC-319D native pairing UI.
- The current version 320 launcher was rebuilt, tested, signed with the preserved
  original update-compatible signing identity, and installed with `adb install
  -r`. The original first-install timestamp remained unchanged; no uninstall or
  app-data wipe occurred.
- The native route is HealthChecker launcher -> Settings -> server address,
  one-time pairing code, and Confirm pairing. The public browser login form is
  intentionally not a pairing surface.
- The production server field was verified as exactly
  `https://health.capitalstratasystems.com` before confirmation.
- Pair confirmation returned HTTP 200; Android recorded `pairing_saved` and
  reported `Paired device: yes`. Pair codes and device tokens were not captured.
- The secure local launcher no longer redirects the S24 away from the native
  pairing form after generating a code.

### Authenticated consumer acceptance

- Robert `00000` authenticated on the real S24 through HC-318 after the approved
  HC-321A local password recovery. No password was supplied through chat or
  command arguments.
- Dashboard, Records, Import/Upload, Trends, Observations, and Settings loaded
  over the production HTTPS origin without WebView or navigation errors.
- Logout returned the S24 to the authentication boundary and revoked companion
  devices for the authenticated owner.
- A reconnect defect was found in Android local cleanup: logout removed the
  device token but retained the active delivery host, producing a partial,
  intentionally fail-closed pairing state. The minimal fix now removes the
  active delivery host, draft host, device ID, and token atomically while
  preserving only the trusted consumer origin.
- Android regression coverage proves logout produces a fully unpaired state,
  retains the trusted consumer origin, and retains no delivery credential or
  draft destination.
- The corrected APK was installed in place, a new owner-scoped pairing completed,
  and Robert authenticated again. Dashboard, Records, Trends, and Observations
  were visible after reconnect with no page error.
- Health Connect background-read permission remains granted and the package's
  WorkManager SystemJobService job remains registered.

### Final regression results

- Android unit tests and debug APK build: **BUILD SUCCESSFUL**.
- Focused HC-318/319/320/321 Python band: **74 passed**.
- Full Python regression: **1238 passed, 3 skipped, 5 subtests passed** in
  437.56 seconds.
- `git diff --check`: PASS before exact staging.

Final gates:

`OWNERSHIP_DISPOSITION=PASS`

`AMBIGUOUS_DATA_QUARANTINED=PASS`

`SYNTHETIC_DATA_EXCLUDED=PASS`

`VAULT_MIGRATION=PASS`

`ENCRYPTED_AT_REST=PASS`

`CLEAN_RESTART=PASS`

`PRODUCTION_TLS=PASS`

`HOST_IDENTITY_VALIDATION=PASS`

`DESKTOP_MOBILE_CONNECTIVITY=PASS`

`MOBILE_RECONNECT=PASS`

`CSS_HEALTHCHECKER_CONCURRENT_ISOLATION=PASS`

`REAL_DEVICE_VALIDATION=PASS`

`REMOTE_SYNC=CONFIRMED`

`RESULT=HC320C_PRODUCTION_RUNTIME_AND_CONNECTIVITY_PASS`

## Authorized Option 1 migration execution

The owner approved the conservative disposition: migrate only explicitly
Robert-scoped material and quarantine/exclude everything else. The migration
was performed by the auditable classified migration utility without changing
the legacy source or its verified snapshot.

- Ownership disposition: PASS.
- Source snapshot revalidated byte-for-byte before target creation.
- Source documents: 6.
- Explicit Robert `00000` documents migrated: 1.
- Synthetic/test documents excluded: 4.
- Ambiguous documents quarantined: 1.
- Migrated measurements: 0; the ambiguous linked measurement was not admitted.
- Migrated timeline events: 0; all 6 unscoped events remain quarantined.
- Explicit Robert observations migrated: 3; 2 unscoped observations remain
  quarantined.
- Explicit Robert trend scopes migrated: 1; 1 unscoped trend remains
  quarantined.
- Explicit Robert alerts migrated: 1; the insufficiently scoped/conflicting
  alert remains quarantined.
- Authentication registry contains only owner account `00000`; the existing
  password hash was preserved, all sessions were revoked, and registry storage
  is HCVE encrypted.
- Production `index.json`, document payload, and authentication registry carry
  authenticated HCVE envelopes. No plaintext production index exists.
- A separately encrypted recovery backup was created outside the repository
  and restored to an isolated validation target. Index and document reads
  reconciled with production.
- Two independent HC-320B factory processes opened and authenticated the vault
  successfully after complete process termination.
- The live runtime subsequently started loopback-only on `127.0.0.1:8766` and
  rejects unauthenticated clinical requests with `401`.

The recovery snapshot and excluded/quarantined source material remain outside
all production user views and are protected under the ProgramData recovery
boundary. No vault, payload, key, recovery key, credential, token, tunnel
credential, or runtime log is included in source control.

## Post-migration connectivity validation

- Dedicated HealthChecker tunnel re-enabled only after the clean-restart gate.
- Trusted `https://health.capitalstratasystems.com/`: `200`.
- Public unauthenticated dashboard request: `401`.
- Controlled tunnel stop/start: PASS; the loopback origin PID remained
  unchanged and `/mobile` returned `200` after reconnect.
- An interactive elevated launcher was found to be coupled to the temporary
  operator process and was terminated externally without an application exit.
  The tunnel was immediately stopped and disabled. The defect was corrected by
  installing the dedicated `HealthCheckerConsumerRuntime` scheduled task under
  the interactive DPAPI owner identity; SYSTEM/service identities are rejected.
- The scheduled runtime survived operator-process termination and passed a
  controlled task stop/start. The production factory reopened the protected
  vault and returned `401` at the clinical boundary after restart. Only then
  was the dedicated tunnel re-enabled.
- CSS remained available on its independent port 8765 during migration,
  runtime startup, tunnel activation, and tunnel reconnect.
- S24 Ultra `R3CX305A7DV` / `SM_S928W`: connected and authorized.
- System-trusted HTTPS from the S24 reached the production host; the protected
  API returned `401` without a session.
- The installed app still held its prior loopback development pairing at the
  start of this acceptance run. Production re-pairing and authenticated UI
  acceptance remain interactive and are not marked passed until completed on
  the unlocked device.

Regression results after migration:

- HC-311/318/319/320 focused security and runtime band: **98 passed**.
- Classified migration transform plus end-to-end atomic backup/restore tests:
  included in the focused result above.
- Android `testDebugUnitTest lintDebug`: **BUILD SUCCESSFUL**.
- Full Python regression: **1232 passed, 3 skipped, 5 subtests passed**.

Current gate state:

`OWNERSHIP_DISPOSITION=PASS`

`AMBIGUOUS_DATA_QUARANTINED=PASS`

`SYNTHETIC_DATA_EXCLUDED=PASS`

`VAULT_MIGRATION=PASS`

`ENCRYPTED_AT_REST=PASS`

`CLEAN_RESTART=PASS`

`PRODUCTION_TLS=PASS`

`CSS_HEALTHCHECKER_CONCURRENT_ISOLATION=PASS`

`REAL_DEVICE_VALIDATION=PENDING_PRODUCTION_PAIRING_AND_LOGIN`

## Reproducible secure-pairing launcher defect and correction

Two local secure credential attempts ended with the same non-secret message:
`HealthChecker did not return a pairing code.` The production transaction was
traced without repeating credential entry.

- Endpoint invoked: `POST /api/companion/pair/start`.
- Loopback production runtime: healthy on `127.0.0.1:8766`; root returned `200`
  and the unauthenticated pairing request returned `401`.
- Robert `00000`: present, `active`, first-password-change complete, and password
  not expired.
- Authentication result from the failed launcher attempt: PASS. The encrypted
  registry contains a successful login audit without credential material.
- Pairing generator and persistence: PASS. Production contains one unconsumed
  pairing session scoped only to `00000`, with no paired production device and
  no other owner scope.
- Sanitized successful API contract: HTTP `200`, `ok=true`, one-time field
  `pair_code`, opaque session identifier, expiry, and TTL. Secret values were
  neither captured in evidence nor logged.
- Root cause: the local prompt checked the nonexistent response property
  `pairing_code`; the established production and Android contract uses
  `pair_code`. Authentication and code generation had succeeded before the
  prompt incorrectly reported failure.
- Correction: the supported local secure pairing script now consumes
  `pair_code`, checks full-session/password state, keeps credential and bearer
  material in process memory only, and surfaces the one-time code solely in the
  visible local window for entry on the S24.
- Regression coverage reproduces the exact response-schema mismatch, confirms
  password-change sessions cannot request pairing, verifies active owner code
  generation, and proves secondary-user pairing sessions remain independently
  scoped.
- Focused HC-318/319/320 band: **59 passed**.
- Full regression after correction: **1235 passed, 3 skipped, 5 subtests
  passed**.

## Activation attempt and fail-closed shutdown

- Cloudflare authorization and zone activation: PASS.
- Dedicated tunnel `healthchecker-production` created with Cloudflare-issued
  UUID `4eaf0cff-862f-418d-b74a-1fbd52532ad9`.
- DNS route for `health.capitalstratasystems.com`: PASS.
- Credentials copied only to the protected ProgramData secret boundary;
  `cert.pem` was not copied, displayed, or committed.
- Dedicated Windows service installation and connector restart: PASS.
- Public system-trusted HTTPS and hostname validation: PASS while connected.
- Unauthenticated clinical API rejection: PASS (`401`).
- CSS/HealthChecker interruption isolation: PASS in both directions.

The production gate then found that port 8766 was owned by a stale uvicorn
process started at `2026-08-17T03:24:47Z`, before this activation. The expected
protected production key `C:\ProgramData\HealthChecker\secrets\vault.key` does
not exist, and a filename-only search found no alternate key under HealthChecker
ProgramData. The repository-default `vault_storage/index.json` used by the stale
runtime does not carry the HCVE encrypted payload marker. A new HC-320B factory
cannot restart from this state and correctly fails closed.

The public HealthChecker tunnel was immediately stopped and its startup type set
to Disabled. CSS remained healthy. The stale loopback process and its data were
not stopped, migrated, overwritten, or deleted, preserving recovery options.

Updated gates:

`PRODUCTION_TLS=PASS`

`HOST_IDENTITY_VALIDATION=PASS`

`DESKTOP_MOBILE_CONNECTIVITY=FAIL` (tunnel intentionally disabled)

`MOBILE_RECONNECT=FAIL` (encrypted production runtime unavailable)

`CSS_HEALTHCHECKER_CONCURRENT_ISOLATION=PASS`

`REAL_DEVICE_VALIDATION=FAIL` (stopped before login to avoid accepting stale plaintext runtime)

Exact blocker: provision a protected production vault key and perform a
controlled, atomic plaintext-to-encrypted migration of the authoritative stale
vault, or restore a verified encrypted backup. The migration must preserve
accounts, Robert `00000` ownership, secondary-user isolation, provenance, and
device state. Do not re-enable the tunnel until the current HC-320B factory
starts successfully from the protected encrypted vault and survives restart.

## Controlled migration recovery assessment

Recovery authorization was received, but the ownership-classification gate did
not pass. No production key or target vault was created, no source file was
changed, and the tunnel remains stopped with automatic startup disabled.

- All HealthChecker application processes capable of writing the legacy vault
  were stopped. Port 8766 has no listener.
- A byte-for-byte recovery snapshot was created at
  `C:\ProgramData\HealthChecker\recovery\hc320c-legacy-vault\20260817T194235Z`.
  All 9 files (411,288 bytes) passed per-file SHA-256 comparison, and the
  snapshot ACL is restricted to Administrators and SYSTEM.
- Source reconciliation: 6 document rows, 6 payload files, no missing payload,
  and no orphan payload.
- The authentication registry contains only account `00000`; its schema and
  presence were inventoried without exposing credential material.
- Exactly 1 document is explicitly owned by Robert `00000` and is eligible for
  migration subject to the later integrity gates.
- Exactly 4 documents are demonstrably synthetic/test records through their
  acquisition provenance. They, 5 linked measurements, 252 linked import-log
  rows, and associated artifacts are excluded from Robert's production history.
- Exactly 1 document remains ambiguous. It is scoped to the legacy
  `default-patient`, was acquired by automatic intake, and requires review; that
  metadata does not establish Robert ownership. Its linked measurement and
  import-log row are also ambiguous.
- Six timeline events, 2 health-intelligence observations, 1 trend, and 1 alert
  remain unscoped or have insufficient/conflicting document linkage. Three
  observations, 1 trend, and 1 alert are explicitly Robert-scoped.
- No clinical values, payload content, password data, tokens, keys, or tunnel
  credentials were written to this evidence.

Required disposition: an authorized reviewer must either affirm that the
ambiguous `default-patient` record group belongs to Robert `00000`, or direct
that it remain quarantined/excluded from the production clinical profile. The
unscoped timeline and intelligence group also requires an explicit disposition
unless an authoritative external ownership source is supplied. Migration,
HCVE verification, clean restart, tunnel re-enablement, real-device acceptance,
commit, and push are blocked until that disposition is provided.

Recovery gate result:

`VAULT_MIGRATION=BLOCKED_OWNERSHIP_DISPOSITION_REQUIRED`

`ENCRYPTED_AT_REST=NOT_RUN`

`CLEAN_RESTART=NOT_RUN`

`PRODUCTION_TLS=PASS_BUT_TUNNEL_DISABLED`

`RESULT=HC320C_PRODUCTION_RUNTIME_AND_CONNECTIVITY_BLOCKED`
