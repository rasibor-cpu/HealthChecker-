plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.healthchecker.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.healthchecker.companion"
        minSdk = 28
        targetSdk = 35
        versionCode = 324
        versionName = "0.324.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL_DEV", "false")
    }

    // Production signing is env-injected only (org-controlled keystore outside Git).
    // Never fall back to the Android debug keystore for release artifacts.
    val signingFile = System.getenv("HC_ANDROID_KEYSTORE_FILE")
    val signingStorePassword = System.getenv("HC_ANDROID_KEYSTORE_PASSWORD")
    val signingKeyAlias = System.getenv("HC_ANDROID_KEY_ALIAS")
    val signingKeyPassword = System.getenv("HC_ANDROID_KEY_PASSWORD")
    val signingEnvValues = listOf(signingFile, signingStorePassword, signingKeyAlias, signingKeyPassword)
    val providedSigningEnvCount = signingEnvValues.count { !it.isNullOrBlank() }
    val externalSigningReady = providedSigningEnvCount == 4
    val requireProductionSigningFlag = System.getenv("HC_ANDROID_REQUIRE_PRODUCTION_SIGNING")
        ?.trim()
        ?.lowercase()
    val requireProductionSigning = requireProductionSigningFlag == "1" ||
        requireProductionSigningFlag == "true" ||
        requireProductionSigningFlag == "yes" ||
        requireProductionSigningFlag == "on"

    if (providedSigningEnvCount in 1..3) {
        throw GradleException(
            "hc_android_signing_env_incomplete: set all four of " +
                "HC_ANDROID_KEYSTORE_FILE, HC_ANDROID_KEYSTORE_PASSWORD, " +
                "HC_ANDROID_KEY_ALIAS, HC_ANDROID_KEY_PASSWORD (or unset all). " +
                "provided_nonblank=$providedSigningEnvCount expected=4. " +
                "Refusing debug-keystore fallback for release."
        )
    }
    if (requireProductionSigning && !externalSigningReady) {
        throw GradleException(
            "hc_android_production_signing_required: HC_ANDROID_REQUIRE_PRODUCTION_SIGNING is set " +
                "but governed HC_ANDROID_* signing material is unavailable. " +
                "Inject org-controlled keystore path + credentials via the approved env interface. " +
                "Never use the debug keystore for production release."
        )
    }
    if (externalSigningReady) {
        val keystorePath = signingFile!!.trim()
        val keystoreFile = file(keystorePath)
        if (!keystoreFile.isFile) {
            throw GradleException(
                "hc_android_keystore_missing: HC_ANDROID_KEYSTORE_FILE does not resolve to a readable " +
                    "keystore file under org key custody (path outside Git). " +
                    "Refusing debug-keystore fallback."
            )
        }
        val normalized = keystoreFile.canonicalFile.absolutePath.replace('\\', '/').lowercase()
        val androidRoot = rootProject.projectDir.canonicalFile.absolutePath.replace('\\', '/').lowercase()
        val repoRoot = rootProject.projectDir.canonicalFile.parentFile.absolutePath.replace('\\', '/').lowercase()
        val forbiddenDebug =
            normalized.endsWith("/debug.keystore") || normalized.contains("/.android/debug.keystore")
        val insideRepo =
            normalized == repoRoot ||
                normalized.startsWith("$repoRoot/") ||
                normalized == androidRoot ||
                normalized.startsWith("$androidRoot/")
        if (forbiddenDebug || insideRepo) {
            throw GradleException(
                "hc_android_keystore_path_forbidden: production keystore must be outside the Git tree " +
                    "and must not be the Android debug keystore. " +
                    "Use an org-controlled custody path via HC_ANDROID_KEYSTORE_FILE."
            )
        }
    }

    signingConfigs {
        if (externalSigningReady) {
            create("production") {
                storeFile = file(signingFile!!.trim())
                storePassword = signingStorePassword
                keyAlias = signingKeyAlias
                keyPassword = signingKeyPassword
            }
        }
    }
    buildTypes {
        debug {
            // Local-dev cleartext is opt-in via manifest network-security-config debug only.
            buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL_DEV", "true")
        }
        release {
            isMinifyEnabled = true
            if (externalSigningReady) {
                signingConfig = signingConfigs.getByName("production")
            }
            // When signing env is absent and REQUIRE is unset: unsigned release is allowed
            // for engineering validation only. Production distribution requires governed signing.
            buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL_DEV", "false")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.health.connect:connect-client:1.1.0-alpha11")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("org.robolectric:robolectric:4.13")
}
