"""Future connector stubs — register when implemented (HC-202 roadmap)."""

from __future__ import annotations

from typing import Any

from backend.ai_health.connectors.base import AIConnector


class GeminiConnector(AIConnector):
    provider_id = "gemini"
    display_name = "Gemini"
    version = "0.0.0-stub"

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("GeminiConnector is reserved for a future release")


class ClaudeConnector(AIConnector):
    provider_id = "claude"
    display_name = "Claude"
    version = "0.0.0-stub"

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("ClaudeConnector is reserved for a future release")


class LocalLLMConnector(AIConnector):
    provider_id = "local_llm"
    display_name = "Local LLM"
    version = "0.0.0-stub"

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("LocalLLMConnector is reserved for a future release")


class MedicalOCRConnector(AIConnector):
    provider_id = "medical_ocr"
    display_name = "Medical OCR"
    version = "0.0.0-stub"

    def normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("MedicalOCRConnector is reserved for a future release")
