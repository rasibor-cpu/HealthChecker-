# HC-317C Regression Reconciliation

Date: 2026-08-16

Branch: `hc311-encrypted-vault-at-rest`
Starting HEAD: `a619fffaa957820988f5526d6c8a8d6dcb1b3e0f`

## Finding

Neither original failure was caused by HC-317C. HC-317C changed the consumer HTML, CSS, dashboard/records JavaScript, service-worker shell and new UI tests; it did not modify the failing health-intelligence or Doctor Visit backend code.

Git history identified two earlier compatibility gaps:

1. HC-315's structured `HealthObservation` serialization replaced, but did not preserve, HC-201C's explicit observational compatibility fields (`observation`, `kind`, `diagnostic`). The safety model remained observational, but the established serialized contract was incomplete.
2. Patient-scoped trend storage was added after `DoctorVisitMode` was written. Doctor Visit accepted `patient_id` but `_trend_line()` still read only the default-patient trend namespace. Its document and timeline selections were also unscoped.

No test was weakened, skipped or edited.

## Minimal compatibility corrections

- Restored additive `HealthObservation.to_dict()` compatibility fields:
  - `observation`: evidence-based fact and interpretation explicitly labelled observational;
  - `kind`: `observational`;
  - `diagnostic`: `false`.
- Made Doctor Visit trend lookup use the requested patient ID.
- Filtered Doctor Visit documents and timeline by the same patient ID, closing the associated cross-patient report leakage risk.
- Preserved the HC-301 stable service-worker cache family constant while using a separate HC-317C revision key for cache invalidation.

## Validation

Original reconciliation targets:

```text
python -m pytest -q \
  tests/test_hc201c_production_readiness.py::test_health_intelligence_observational \
  tests/test_hc201f_backfill.py::test_doctor_visit_includes_records
2 passed in 1.73s
```

Related HC-315 coverage passed during investigation. The first aggregate run also exposed the HC-301 cache-name compatibility assertion and one transient HC-309 process-observation failure. After retaining the stable cache family name, both tests passed together in isolation. No HC-309 implementation change was made.

Final full regression:

```text
python -m pytest -q
1164 passed, 3 skipped, 2 warnings, 5 subtests passed in 408.84s
```

The warnings are existing dependency deprecations in FastAPI/Starlette TestClient and HTTPX request encoding.

## Conclusion

The regression gate is reconciled. The compatibility changes are narrow, additive, patient-safe and necessary to preserve existing HC-201/HC-301 contracts alongside HC-315/HC-317 behavior.
