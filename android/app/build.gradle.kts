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
        versionCode = 320
        versionName = "0.320.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("boolean", "ALLOW_CLEARTEXT_LOCAL_DEV", "false")
    }

    val signingFile = System.getenv("HC_ANDROID_KEYSTORE_FILE")
    val signingStorePassword = System.getenv("HC_ANDROID_KEYSTORE_PASSWORD")
    val signingKeyAlias = System.getenv("HC_ANDROID_KEY_ALIAS")
    val signingKeyPassword = System.getenv("HC_ANDROID_KEY_PASSWORD")
    val externalSigningReady = listOf(signingFile, signingStorePassword, signingKeyAlias, signingKeyPassword)
        .all { !it.isNullOrBlank() }
    signingConfigs {
        if (externalSigningReady) {
            create("production") {
                storeFile = file(signingFile!!)
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
            if (externalSigningReady) signingConfig = signingConfigs.getByName("production")
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
