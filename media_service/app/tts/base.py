"""TTSProvider interface shared by all text-to-speech backends."""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TTSRequest:
    text: str  # already pronunciation-mapped; what the voice actually says
    voice_id: str
    language: str = "vi"
    speed: float = 1.0
    seed: int | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class TTSResult:
    audio: bytes
    format: str  # "wav" | "mp3" | ...
    words: list[dict[str, Any]] | None = None  # [{word, start, end}] if the API gives them


class TTSError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        self.retryable = retryable
        super().__init__(message)


class TTSProvider(ABC):
    name: str = "base"
    supports_word_timestamps: bool = False

    @abstractmethod
    def synthesize(self, request: TTSRequest) -> TTSResult:
        """Return audio for one request. Raise TTSError on failure."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return {"ok": bool, "message": str, ...}."""

    def cache_key(self, request: TTSRequest) -> str:
        """Stable hash of everything that changes the produced audio."""
        payload = {"provider": self.name, **asdict(request), **self.cache_identity()}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def cache_identity(self) -> dict[str, Any]:
        """Provider settings that affect output (e.g. model name)."""
        return {}
