# HealthChecker+ Android Companion (HC-303A)

Kotlin/Gradle module for Health Connect → secure host delivery.

## Tooling required

- JDK 17+
- Android SDK (compileSdk 35)
- Gradle 8.x (or Android Studio)

This repository does **not** auto-install those tools.

## Commands

```bash
./gradlew :app:testDebugUnitTest
./gradlew :app:lintDebug
./gradlew :app:assembleDebug
```

On Windows without a wrapper yet, use Android Studio “Open” on this `android/` folder to generate the wrapper, or install Gradle manually.

See `docs/HC303A_ANDROID_COMPANION.md` for pairing, permissions, and device validation.
