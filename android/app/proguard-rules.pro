# Keep companion classes; strip logging of secrets in release.
-keep class com.healthchecker.companion.** { *; }
-dontwarn okhttp3.**
