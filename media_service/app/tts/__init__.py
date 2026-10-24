from app.config import BrandConfig, Settings
from app.tts.base import TTSError, TTSProvider, TTSRequest, TTSResult


def get_provider(settings: Settings, brand: BrandConfig) -> TTSProvider:
    cfg = brand.tts
    if cfg.provider == "voice_studio":
        from app.tts.voice_studio import VoiceStudioProvider

        return VoiceStudioProvider(
            base_url=settings.voice_studio_url, voice_id=settings.voice_studio_voice_id,
            api_key=settings.voice_studio_api_key, model=cfg.model, timeout_s=cfg.timeout_s,
            max_retries=cfg.max_retries,
        )
    if cfg.provider == "elevenlabs":
        from app.tts.elevenlabs import ElevenLabsProvider

        return ElevenLabsProvider()
    raise TTSError(f"TTS provider không hợp lệ: {cfg.provider}")


__all__ = ["TTSError", "TTSProvider", "TTSRequest", "TTSResult", "get_provider"]
