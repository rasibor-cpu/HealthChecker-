package com.healthchecker.companion.healthconnect

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.BloodPressureRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.WeightRecord

/**
 * Discover Health Connect availability and permission deltas.
 * ECG is intentionally unsupported in HC-303A.
 */
class HealthConnectCapability(private val context: Context) {

    fun requiredPermissions(): Set<String> = setOf(
        HealthPermission.getReadPermission(HeartRateRecord::class),
        HealthPermission.getReadPermission(RestingHeartRateRecord::class),
        HealthPermission.getReadPermission(OxygenSaturationRecord::class),
        HealthPermission.getReadPermission(BloodPressureRecord::class),
        HealthPermission.getReadPermission(SleepSessionRecord::class),
        HealthPermission.getReadPermission(StepsRecord::class),
        HealthPermission.getReadPermission(ExerciseSessionRecord::class),
        HealthPermission.getReadPermission(WeightRecord::class)
    )

    suspend fun report(): CapabilityReport {
        val status = HealthConnectClient.getSdkStatus(context)
        return when (status) {
            HealthConnectClient.SDK_UNAVAILABLE -> CapabilityReport(
                availability = HealthConnectAvailability.UNAVAILABLE,
                sdkAvailable = false,
                message = "Health Connect is unavailable on this device."
            )
            HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> CapabilityReport(
                availability = HealthConnectAvailability.UPDATE_REQUIRED,
                sdkAvailable = false,
                message = "Health Connect provider update is required."
            )
            HealthConnectClient.SDK_AVAILABLE -> {
                val client = HealthConnectClient.getOrCreate(context)
                val granted = client.permissionController.getGrantedPermissions()
                val required = requiredPermissions()
                val missing = required - granted
                CapabilityReport(
                    availability = HealthConnectAvailability.READY,
                    sdkAvailable = true,
                    message = if (missing.isEmpty()) {
                        "Health Connect ready with required permissions granted."
                    } else {
                        "Health Connect available; permissions missing: ${missing.size}."
                    },
                    permissionsGranted = granted,
                    permissionsMissing = missing,
                    ecgSupported = false
                )
            }
            else -> CapabilityReport(
                availability = HealthConnectAvailability.ERROR,
                sdkAvailable = false,
                message = "Unrecognized Health Connect SDK status: $status"
            )
        }
    }
}
