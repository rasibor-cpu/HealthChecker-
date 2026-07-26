# HealthChecker+
HealthChecker+ Web App (iOS Compatible Version)

## HC-201 Health Vault

Additive longitudinal medical record layer. See [docs/HC201_HEALTH_VAULT.md](docs/HC201_HEALTH_VAULT.md).

```bash
python -m pytest tests/test_hc201_health_vault.py -q
```

## HC-301 Always-On Health Guardian

Observational Guardian layer: alerts, baselines, CGM continuity, PWA service-worker foundation. See [docs/HC301_ALWAYS_ON_HEALTH_GUARDIAN.md](docs/HC301_ALWAYS_ON_HEALTH_GUARDIAN.md).

```bash
python -m pytest tests/test_hc301_health_guardian.py -q
```

Manual recent-record import (script only, never on startup):

```bash
python scripts/import_recent_hc301_records.py
```
