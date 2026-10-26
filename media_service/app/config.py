"""Central configuration: environment settings + brand.yaml / pronunciation.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

BackgroundKind = Literal["kenburns", "loop", "gradient"]


class Settings(BaseSettings):
    """Process-level settings from environment / .env."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    config_dir: Path = REPO_ROOT / "config"
    assets_dir: Path = REPO_ROOT / "assets"
    remotion_dir: Path = REPO_ROOT / "remotion"

    voice_studio_url: str = ""
    voice_studio_api_key: str = ""
    voice_studio_voice_id: str = ""

    # LLM for metadata / clip selection. claude_code = Claude Code CLI on a subscription
    # token (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`).
    llm_provider: Literal["claude_code"] = "claude_code"
    llm_model: str = "sonnet"
    llm_timeout_s: float = 300
    claude_code_oauth_token: str = ""

    whisper_device: Literal["auto", "cuda", "cpu"] = "auto"
    whisper_model: str = "large-v3-turbo"

    max_concurrent_jobs: int = 1
    # base URL used in links sent to Telegram (must be reachable from your phone)
    media_public_url: str = "http://localhost:8000"
    # n8n as seen from this container (health check, approving drafts from the web UI)
    n8n_internal_url: str = "http://n8n:5678"

    # TikTok Content Posting API (Direct Post). Redirect URI must be https (tunnel).
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""
    # inbox = drafts in the creator's TikTok inbox (works for unaudited apps on public
    # accounts); direct = post directly (needs an audited app or a private account)
    tiktok_mode: Literal["inbox", "direct"] = "inbox"
    tiktok_privacy: str = "SELF_ONLY"  # direct mode only
    tiktok_max_clips: int = 3
    log_level: str = "INFO"

    @property
    def episodes_dir(self) -> Path:
        return self.data_dir / "episodes"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def music_dir(self) -> Path:
        return self.assets_dir / "music"

    @property
    def backgrounds_dir(self) -> Path:
        return self.assets_dir / "backgrounds"


# --- brand.yaml -----------------------------------------------------------------


class ChannelCfg(BaseModel):
    name: str = "Podcast Studio"
    logo: str | None = "logo.png"


class ColorsCfg(BaseModel):
    primary: str = "#F5B700"
    background: str = "#0F172A"
    text: str = "#F8FAFC"
    muted: str = "#94A3B8"
    gradient: list[str] = Field(default_factory=lambda: ["#0F172A", "#1E3A8A", "#4C1D95"])


class FontsCfg(BaseModel):
    family: str = "Be Vietnam Pro"
    regular: str = "fonts/BeVietnamPro-Regular.ttf"
    bold: str = "fonts/BeVietnamPro-Bold.ttf"


class VideoCfg(BaseModel):
    background: BackgroundKind = "gradient"
    fps: int = 30


class ScriptCfg(BaseModel):
    long_sentence_warn_chars: int = 300
    max_pause_seconds: float = 30
    music_up_seconds: float = 3


class TTSCfg(BaseModel):
    provider: Literal["voice_studio", "elevenlabs"] = "voice_studio"
    model: str = "omnivoice"
    language: str = "vi"
    speed: float = 1.0
    seed: int | None = 42
    concurrency: int = 2
    max_chars_per_request: int = 400
    timeout_s: float = 300
    max_retries: int = 5
    voices: dict[str, str] = Field(default_factory=dict)  # speaker name -> voice id
    group_max_chars: int = 170  # read up to this many chars of one paragraph per request; 0 = per sentence
    min_word_match: float = 0.93  # a take covering less of the script is redone with the next seed
    seed_attempts: int = 3
    sampling: dict[str, Any] = Field(default_factory=dict)  # extra Voice Studio /generate fields


class GapsCfg(BaseModel):
    sentence: int = 200
    paragraph: int = 450
    chapter: int = 1200


class CompressorCfg(BaseModel):
    enabled: bool = True
    threshold_db: float = -18
    ratio: float = 2
    attack_ms: float = 20
    release_ms: float = 250


class LoudnessCfg(BaseModel):
    target_lufs: float = -16
    true_peak_db: float = -1.5
    lra: float = 11


class MusicCfg(BaseModel):
    enabled: bool = True
    level_db: float = -22
    duck_db: float = 10
    duck_attack_ms: float = 20
    duck_release_ms: float = 1200
    fade_in_s: float = 3
    fade_out_s: float = 5
    intro: str | None = None
    outro: str | None = None
    crossfade_s: float = 1.0


class AudioCfg(BaseModel):
    sample_rate: int = 48000
    gaps_ms: GapsCfg = Field(default_factory=GapsCfg)
    lead_in_ms: int = 500
    tail_ms: int = 1000
    trim_silence_db: float = -40
    highpass_hz: float = 60
    compressor: CompressorCfg = Field(default_factory=CompressorCfg)
    loudness: LoudnessCfg = Field(default_factory=LoudnessCfg)
    music: MusicCfg = Field(default_factory=MusicCfg)
    mp3_bitrate: str = "192k"


class SubtitlesCfg(BaseModel):
    max_chars_per_line: int = 42
    max_lines: int = 2
    karaoke_min_words: int = 2
    karaoke_max_words: int = 4


class ShortsCfg(BaseModel):
    min_clips: int = 3
    max_clips: int = 5
    min_seconds: float = 30
    max_seconds: float = 60


class BrandConfig(BaseModel):
    channel: ChannelCfg = Field(default_factory=ChannelCfg)
    colors: ColorsCfg = Field(default_factory=ColorsCfg)
    fonts: FontsCfg = Field(default_factory=FontsCfg)
    video: VideoCfg = Field(default_factory=VideoCfg)
    script: ScriptCfg = Field(default_factory=ScriptCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    audio: AudioCfg = Field(default_factory=AudioCfg)
    subtitles: SubtitlesCfg = Field(default_factory=SubtitlesCfg)
    shorts: ShortsCfg = Field(default_factory=ShortsCfg)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_brand(config_dir: Path | None = None) -> BrandConfig:
    path = (config_dir or get_settings().config_dir) / "brand.yaml"
    if not path.exists():
        return BrandConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return BrandConfig.model_validate(data)


def load_pronunciation(config_dir: Path | None = None) -> dict[str, str]:
    path = (config_dir or get_settings().config_dir) / "pronunciation.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in data.items()}
