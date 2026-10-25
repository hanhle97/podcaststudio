"""faster-whisper wrapper used only for word timing (never for the text itself)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class WhisperWord:
    word: str
    start: float
    end: float
    probability: float = 1.0


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - missing libs simply mean "no GPU"
        return False


def pick_device(preference: str) -> tuple[str, str]:
    """(device, compute_type) for faster-whisper. auto = CUDA if usable, else CPU."""
    if preference == "cpu":
        return "cpu", "int8"
    if preference == "cuda" or cuda_available():
        return "cuda", "float16"
    return "cpu", "int8"


_models: dict[tuple[str, str], object] = {}
_lock = threading.Lock()


class WhisperTimer:
    """Loads a model once per process; falls back to CPU if CUDA fails in auto mode."""

    def __init__(self, model_name: str, preference: str = "auto"):
        self.model_name = model_name
        self.preference = preference
        self.device, self.compute_type = pick_device(preference)
        self.fallback_reason: str | None = None

    def _model(self):
        from faster_whisper import WhisperModel

        key = (self.model_name, self.device)
        with _lock:
            if key not in _models:
                log.info("Tải faster-whisper %s trên %s (%s)", self.model_name, self.device,
                         self.compute_type)
                _models[key] = WhisperModel(self.model_name, device=self.device,
                                            compute_type=self.compute_type)
            return _models[key]

    def words(self, audio: Path, prompt: str | None = None,
              language: str = "vi") -> list[WhisperWord]:
        try:
            return self._transcribe(audio, prompt, language)
        except Exception as e:  # noqa: BLE001
            if self.device != "cuda" or self.preference == "cuda":
                raise
            self.fallback_reason = f"CUDA lỗi ({type(e).__name__}: {e}), chuyển sang CPU."
            log.warning(self.fallback_reason)
            self.device, self.compute_type = "cpu", "int8"
            return self._transcribe(audio, prompt, language)

    def _transcribe(self, audio: Path, prompt: str | None, language: str) -> list[WhisperWord]:
        segments, _info = self._model().transcribe(
            str(audio), language=language, word_timestamps=True, initial_prompt=prompt,
            beam_size=5, vad_filter=False, condition_on_previous_text=False,
        )
        out: list[WhisperWord] = []
        for seg in segments:
            for w in seg.words or []:
                out.append(WhisperWord(w.word.strip(), float(w.start), float(w.end),
                                       float(w.probability)))
        return out
