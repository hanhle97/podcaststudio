"""ElevenLabs provider – stub only, kept so the interface stays swappable."""

from __future__ import annotations

from typing import Any

from app.tts.base import TTSError, TTSProvider, TTSRequest, TTSResult


class ElevenLabsProvider(TTSProvider):
    name = "elevenlabs"
    supports_word_timestamps = True  # the real API can return character alignment

    def __init__(self, **_: Any):
        pass

    def synthesize(self, request: TTSRequest) -> TTSResult:
        raise TTSError("Provider 'elevenlabs' chưa được triển khai. "
                       "Đặt tts.provider: voice_studio trong config/brand.yaml.")

    def health(self) -> dict[str, Any]:
        return {"ok": False, "provider": self.name, "message": "Provider 'elevenlabs' chưa "
                "được triển khai."}
