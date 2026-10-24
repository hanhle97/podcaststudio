"""TTS step: one 48 kHz mono WAV per script sentence, with a content-hash cache.

Cache: data/cache/tts/{sha256}.wav keyed on (provider, model, voice, text, speed,
seed, language). Editing one sentence only calls the API for that sentence.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.audio.ffmpeg import run_ffmpeg, wav_duration
from app.audio.filters import normalize_format, trim_filter
from app.config import load_pronunciation
from app.episodes import write_json
from app.jobs import StepError
from app.tts import TTSError, TTSProvider, TTSRequest, get_provider
from app.tts.text import apply_pronunciation, chunk_text

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress

log = logging.getLogger(__name__)


@dataclass
class SentencePlan:
    id: str
    line: int
    text: str
    speech_text: str
    voice_id: str
    requests: list[TTSRequest]
    keys: list[str]


def tts_dir(env: StepEnv) -> Path:
    return env.paths.root / "tts"


def manifest_path(env: StepEnv) -> Path:
    return tts_dir(env) / "manifest.json"


def cache_dir(env: StepEnv) -> Path:
    return env.settings.cache_dir / "tts"


def plan_sentences(env: StepEnv, provider: TTSProvider) -> tuple[list[SentencePlan], list[str]]:
    cfg = env.brand.tts
    pronunciation = load_pronunciation(env.settings.config_dir)
    default_voice = env.settings.voice_studio_voice_id
    warnings: list[str] = []
    plans: list[SentencePlan] = []
    for s in env.script.sentences:
        voice = default_voice
        if s.speaker:
            voice = cfg.voices.get(s.speaker, default_voice)
            if s.speaker not in cfg.voices:
                msg = (f"Người nói '{s.speaker}' chưa có giọng trong brand.yaml (tts.voices), "
                       "dùng giọng mặc định.")
                if msg not in warnings:
                    warnings.append(msg)
        speech = apply_pronunciation(s.text, pronunciation)
        requests = [TTSRequest(text=chunk, voice_id=voice, language=cfg.language,
                               speed=cfg.speed, seed=cfg.seed)
                    for chunk in chunk_text(speech, cfg.max_chars_per_request)]
        plans.append(SentencePlan(id=s.id, line=s.line, text=s.text, speech_text=speech,
                                  voice_id=voice, requests=requests,
                                  keys=[provider.cache_key(r) for r in requests]))
    return plans, warnings


def run_tts(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    try:
        provider = get_provider(env.settings, env.brand)
    except TTSError as e:
        raise StepError("tts", str(e)) from e
    health = provider.health()
    if not health.get("ok"):
        raise StepError("tts", health.get("message", "Voice Studio không sẵn sàng."))

    plans, warnings = plan_sentences(env, provider)
    cache = cache_dir(env)
    cache.mkdir(parents=True, exist_ok=True)
    out_dir = tts_dir(env)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) fetch every chunk not yet cached (deduplicated), bounded parallelism
    todo: dict[str, tuple[TTSRequest, SentencePlan]] = {}
    for plan in plans:
        for key, req in zip(plan.keys, plan.requests):
            if not (cache / f"{key}.wav").exists():
                todo.setdefault(key, (req, plan))
    total = len(todo)
    progress(0.0, f"{len(plans)} câu, {total} đoạn cần gọi Voice Studio "
                  f"({sum(len(p.keys) for p in plans) - total} lấy từ cache).")

    def fetch(key: str, req: TTSRequest) -> None:
        result = provider.synthesize(req)
        tmp = cache / f"{key}.{result.format}.tmp"
        tmp.write_bytes(result.audio)
        tmp.replace(cache / f"{key}.{result.format}")
        if result.words:
            write_json(cache / f"{key}.words.json", result.words)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, env.brand.tts.concurrency)) as pool:
        futures = {pool.submit(fetch, key, req): plan for key, (req, plan) in todo.items()}
        for fut in as_completed(futures):
            plan = futures[fut]
            try:
                fut.result()
            except TTSError as e:
                for other in futures:
                    other.cancel()
                raise StepError("tts", f"Câu ở dòng {plan.line} ({plan.text[:40]}…): {e}") from e
            done += 1
            progress(0.85 * done / max(total, 1), f"Đã tạo giọng {done}/{total} đoạn.")

    # 2) build per-sentence WAVs (48 kHz mono, edges trimmed); reuse if chunks unchanged
    old = _load_manifest(env)
    manifest: dict[str, Any] = {"provider": provider.name, "sentences": {}}
    a = env.brand.audio
    afilter = f"{trim_filter(a.trim_silence_db)},{normalize_format(a.sample_rate)}"
    for i, plan in enumerate(plans):
        target = out_dir / f"{plan.id}.wav"
        prev = old.get(plan.id)
        if not (prev and prev.get("keys") == plan.keys and target.exists()
                and prev.get("filter") == afilter):
            inputs: list[str] = []
            for key in plan.keys:
                inputs += ["-i", str(cache / f"{key}.wav")]
            n = len(plan.keys)
            graph = "".join(f"[{k}:a]" for k in range(n)) + f"concat=n={n}:v=0:a=1,{afilter}"
            run_ffmpeg([*inputs, "-filter_complex", graph, "-c:a", "pcm_s16le", str(target)],
                       "tts", f"chuẩn hóa audio câu {plan.id}")
        manifest["sentences"][plan.id] = {
            "file": f"tts/{plan.id}.wav", "duration": round(wav_duration(target), 3),
            "text": plan.text, "speech_text": plan.speech_text, "voice_id": plan.voice_id,
            "keys": plan.keys, "filter": afilter,
        }
        progress(0.85 + 0.15 * (i + 1) / len(plans))

    # remove WAVs of sentences that no longer exist
    for f in out_dir.glob("s*.wav"):
        if f.stem not in manifest["sentences"]:
            f.unlink()
    write_json(manifest_path(env), manifest)
    return {"sentences": len(plans), "api_calls": total, "warnings": warnings}


def _load_manifest(env: StepEnv) -> dict[str, Any]:
    path = manifest_path(env)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("sentences", {})
