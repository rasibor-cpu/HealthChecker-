"""HC-313A Gmail medical-record acquisition package.

Architecture:
    Gmail → GmailAcquirer → (GmailClassifier + PatientIdentityVerifier)
          → hc_intake/incoming/ → HC-312 → ImportService → HC-311 Vault

PRODUCTION SAFETY:
    SCHEDULED_GMAIL_POLLING_INSTALLED=NO
    LIVE_GMAIL_ACQUISITION_ACTIVATED=NO
    PRODUCTION_GMAIL_CREDENTIAL_CREATED=NO
"""

from backend.health_vault.acquisition.gmail_models import (
    AcquisitionDecision,
    AcquisitionRecord,
    GmailAttachment,
    GmailAttachmentRetrievalError,
    GmailConnectorError,
    GmailMessage,
    IdentityReasonCode,
    MedicalClassification,
    PatientIdentityResult,
)
from backend.health_vault.acquisition.gmail_config import GmailAcquisitionConfig, get_default_config
from backend.health_vault.acquisition.gmail_classifier import GmailClassifier, ClassificationResult
from backend.health_vault.acquisition.patient_identity import PatientIdentityVerifier, normalize_name
from backend.health_vault.acquisition.acquisition_state import AcquisitionStateStore
from backend.health_vault.acquisition.gmail_acquirer import (
    GmailAcquirer,
    GmailConnectorProtocol,
    MockGmailConnector,
    AcquisitionSummary,
)

__all__ = [
    # Models
    "AcquisitionDecision",
    "AcquisitionRecord",
    "GmailAttachment",
    "GmailAttachmentRetrievalError",
    "GmailConnectorError",
    "GmailMessage",
    "IdentityReasonCode",
    "MedicalClassification",
    "PatientIdentityResult",
    # Config
    "GmailAcquisitionConfig",
    "get_default_config",
    # Classifier
    "ClassificationResult",
    "GmailClassifier",
    # Identity
    "normalize_name",
    "PatientIdentityVerifier",
    # State
    "AcquisitionStateStore",
    # Acquirer
    "AcquisitionSummary",
    "GmailAcquirer",
    "GmailConnectorProtocol",
    "MockGmailConnector",
]
