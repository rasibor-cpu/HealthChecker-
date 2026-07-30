package com.healthchecker.companion.healthconnect

import android.content.Context
import androidx.health.connect.client.HealthConnectClient

/**
 * Discover Health Connect availability and permission deltas.
 * ECG is intentionally unsupported in HC-303A.
 *
 * HC-306I-R3: supported permission set comes from [GrantedRecordCatalog].
 * Sync may proceed with a partial grant; UI still reports missing types.
 */
class HealthConnectCapability(private val context: Context) {

    fun requiredPermissions(): Set<String> = GrantedRecordCatalog.allReadPermissions()

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
                val grantedRaw = client.permissionController.getGrantedPermissions()
                val required = requiredPermissions()
                val granted = grantedRaw.intersect(required)
                val missing = required - granted
                CapabilityReport(
                    availability = HealthConnectAvailability.READY,
                    sdkAvailable = true,
                    message = when {
                        missing.isEmpty() ->
                            "Health Connect ready with required permissions granted."
                        granted.isEmpty() ->
                            "Health Connect available; no supported read permissions granted."
                        else ->
                            "Health Connect available; syncing granted types only " +
                                "(${granted.size} granted, ${missing.size} missing)."
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
