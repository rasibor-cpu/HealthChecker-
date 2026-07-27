"""
HC-303A — Android companion secure pairing and observation delivery.

Additive host-side trust boundary for the HealthChecker+ Android companion.
Observations are delivered into the canonical HC-302 IngestionCoordinator.
No simulated observations are accepted on production companion endpoints.
"""

from backend.health_vault.companion.pairing import CompanionPairingService
from backend.health_vault.companion.delivery import CompanionDeliveryService

__all__ = [
    "CompanionPairingService",
    "CompanionDeliveryService",
]
