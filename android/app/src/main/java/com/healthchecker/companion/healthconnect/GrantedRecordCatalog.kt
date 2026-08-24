package com.healthchecker.companion.healthconnect

import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BloodGlucoseRecord
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord
import kotlin.reflect.KClass

/**
 * HC-306I-R3 — maps supported Health Connect read permissions to record classes
 * and privacy-safe token-scope fingerprints (sorted scope ids only).
 *
 * ECG intentionally unsupported. Glucose is Health Connect BloodGlucoseRecord only.
 */
object GrantedRecordCatalog {

    enum class Metric(val scopeId: String) {
        HEART_RATE("heart_rate"),
        RESTING_HEART_RATE("resting_heart_rate"),
        SPO2("spo2"),
        BLOOD_PRESSURE("blood_pressure"),
        SLEEP("sleep"),
        STEPS("steps"),
        EXERCISE("exercise"),
        WEIGHT("weight"),
        GLUCOSE("glucose")
    }

    data class Binding(
        val metric: Metric,
        val recordClass: KClass<out Record>
    ) {
        val readPermission: String
            get() = HealthPermission.getReadPermission(recordClass)
    }

    val bindings: List<Binding> = listOf(
        Binding(Metric.HEART_RATE, HeartRateRecord::class),
        Binding(Metric.RESTING_HEART_RATE, RestingHeartRateRecord::class),
        Binding(Metric.SPO2, OxygenSaturationRecord::class),
        Binding(Metric.BLOOD_PRESSURE, BloodPressureRecord::class),
        Binding(Metric.SLEEP, SleepSessionRecord::class),
        Binding(Metric.STEPS, StepsRecord::class),
        Binding(Metric.EXERCISE, ExerciseSessionRecord::class),
        Binding(Metric.WEIGHT, WeightRecord::class),
        Binding(Metric.GLUCOSE, BloodGlucoseRecord::class)
    )

    private val byPermission: Map<String, Binding> =
        bindings.associateBy { it.readPermission }

    private val byScopeId: Map<String, Binding> =
        bindings.associateBy { it.metric.scopeId }

    fun allReadPermissions(): Set<String> = bindings.map { it.readPermission }.toSet()

    fun bindingForPermission(permission: String): Binding? = byPermission[permission]

    fun grantedBindings(grantedPermissions: Set<String>): List<Binding> =
        bindings.filter { it.readPermission in grantedPermissions }

    fun missingPermissions(grantedPermissions: Set<String>): Set<String> =
        allReadPermissions() - grantedPermissions

    fun recordClasses(grantedPermissions: Set<String>): Set<KClass<out Record>> =
        grantedBindings(grantedPermissions).map { it.recordClass }.toSet()

    /** Stable, privacy-safe fingerprint of the granted type set used for a changes token. */
    fun scopeFingerprint(grantedPermissions: Set<String>): String {
        val ids = grantedBindings(grantedPermissions).map { it.metric.scopeId }.sorted()
        return ids.joinToString(",")
    }

    fun parseScopeFingerprint(raw: String?): Set<String>? {
        if (raw == null) return null
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return emptySet()
        val parts = trimmed.split(',').map { it.trim() }.filter { it.isNotEmpty() }
        if (parts.isEmpty()) return emptySet()
        if (parts.any { it !in byScopeId }) return null // corrupt / unknown id
        return parts.toSet()
    }

    /** Valid for persistence: known metric ids and non-empty. Blank/corrupt → false. */
    fun isValidScopeFingerprint(raw: String?): Boolean {
        val parsed = parseScopeFingerprint(raw) ?: return false
        return parsed.isNotEmpty()
    }

    fun scopeMatches(persistedScope: String?, currentFingerprint: String): Boolean {
        val parsed = parseScopeFingerprint(persistedScope) ?: return false
        val current = parseScopeFingerprint(currentFingerprint) ?: return false
        return parsed == current
    }

    fun includesSteps(grantedPermissions: Set<String>): Boolean =
        bindings.first { it.metric == Metric.STEPS }.readPermission in grantedPermissions
}
