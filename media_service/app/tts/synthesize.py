"""TTS step: one 48 kHz mono WAV per script sentence, with a content-hash cache.

Sentences of one paragraph are read together, up to tts.group_max_chars per take, so
the voice keeps one rhythm instead of restarting at every full stop. Every take is
checked with faster-whisper; a take that skips words is redone with the next seed,
and a group that keeps skipping is read one sentence at a time. The take is then cut
back into per-sentence WAVs at the quietest point between sentences, so everything
downstream (timeline, alignment, subtitles, clips) still works per sentence.

Cache: data/cache/tts/{sha256}.wav per request, keyed on (provider, model, voice, text,
speed, seed, language, sampling); {group}.48k.wav + {group}.check.json per checked take.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from app.align.align import align_tokens, normalize_token, spoken_tokens
from app.align.whisper import WhisperTimer, WhisperWord
from app.audio.assemble import read_wav_mono16, write_wav_mono16
from app.audio.ffmpeg import run_ffmpeg
from app.audio.filters import normalize_format, trim_filter
from app.config import load_pronunciation
from app.episodes import write_json
from app.jobs import StepError
from app.script import Script, Sentence
from app.tts import TTSError, TTSProvider, TTSRequest, get_provider
from app.tts.text import chunk_text, speakable

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress

log = logging.getLogger(__name__)

EDGE_KEEP_S = 0.04  # silence kept at both ends of a take
MIN_PIECE_S = 0.15  # a cut that leaves a sentence shorter than this is not trusted


@dataclass
class SentencePlan:
    id: str
    line: int
    text: str
    speech_text: str
    voice_id: str


@dataclass
class Group:
    sentences: list[SentencePlan]

    @property
    def voice_id(self) -> str:
        return self.sentences[0].voice_id

    @property
    def speech_text(self) -> str:
        return " ".join(s.speech_text for s in self.sentences)


@dataclass
class Take:
    seed: int | None
    keys: list[str]
    wav: Path
    score: float
    cuts: list[float] = field(default_factory=list)  # start (s) of every sentence after the first


def tts_dir(env: StepEnv) -> Path:
    return env.paths.root / "tts"


def manifest_path(env: StepEnv) -> Path:
    return tts_dir(env) / "manifest.json"


def cache_dir(env: StepEnv) -> Path:
    return env.settings.cache_dir / "tts"


def plan_groups(script: Script, voices: dict[str, str], default_voice: str,
                pronunciation: dict[str, str], max_chars: int) -> tuple[list[Group], list[str]]:
    """Consecutive sentences of one paragraph and voice, at most `max_chars` per group.

    Tags ([pause], [music up]) and chapter starts always end a group. max_chars <= 0
    gives one sentence per group.
    """
    warnings: list[str] = []
    groups: list[Group] = []
    for chapter in script.chapters:
        current: Group | None = None
        prev: Sentence | None = None
        for item in chapter.items:
            if not isinstance(item, Sentence):
                current, prev = None, None
                continue
            voice = default_voice
            if item.speaker:
                voice = voices.get(item.speaker, default_voice)
                if item.speaker not in voices:
                    msg = (f"Người nói '{item.speaker}' chưa có giọng trong brand.yaml "
                           "(tts.voices), dùng giọng mặc định.")
                    if msg not in warnings:
                        warnings.append(msg)
            plan = SentencePlan(id=item.id, line=item.line, text=item.text,
                                speech_text=speakable(item.text, pronunciation), voice_id=voice)
            fits = (current is not None and prev is not None
                    and prev.paragraph == item.paragraph and current.voice_id == voice
                    and len(current.speech_text) + 1 + len(plan.speech_text) <= max_chars)
            if fits:
                current.sentences.append(plan)
            else:
                current = Group([plan])
                groups.append(current)
            prev = item
    return groups, warnings


def expand_heard(words: list[WhisperWord], pronunciation: dict[str, str]) -> list[WhisperWord]:
    """Whisper writes "n8n" / "API" as such; turn them into the spoken tokens we compare to."""
    spoken = {normalize_token(k): v.split() for k, v in pronunciation.items() if normalize_token(k)}
    out: list[WhisperWord] = []
    for w in words:
        parts = spoken.get(normalize_token(w.word))
        if not parts:
            out.append(w)
            continue
        step = (w.end - w.start) / len(parts)
        out += [WhisperWord(p, w.start + i * step, w.start + (i + 1) * step, w.probability)
                for i, p in enumerate(parts)]
    return out


def coverage(script_tokens: list[str], heard: list[WhisperWord]) -> float:
    """Share of the script's letters that the voice actually said.

    Only skips count against a take: a word whisper hears differently ("NAN" for
    "en ây en", "lập" for "lặp") is replaced by something of similar length, while a
    skipped phrase leaves script letters with nothing heard in their place.
    """
    heard_tokens = [t for t in (normalize_token(w.word) for w in heard) if t]
    total = sum(len(t) for t in script_tokens)
    if not total:
        return 0.0
    missing = 0
    matcher = SequenceMatcher(None, script_tokens, heard_tokens, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        said = sum(len(t) for t in script_tokens[i1:i2])
        got = sum(len(t) for t in heard_tokens[j1:j2])
        if op == "delete":
            missing += said
        elif op == "replace":
            missing += max(0, said - got)
    return 1 - missing / total


def score_and_cuts(group: Group, words: list[WhisperWord], samples: np.ndarray, rate: int,
                   pronunciation: dict[str, str]) -> tuple[float, list[float]]:
    """How much of the script the take says, and where each later sentence starts.

    A cut sits at the quietest 10 ms between the last word of one sentence and the
    first word of the next. An untrustworthy cut scores 0 so the group is redone.
    """
    tokens: list[tuple[str, int]] = []
    for si, sent in enumerate(group.sentences):
        tokens += [(tok, si) for tok, _ in spoken_tokens(sent.text.split(), pronunciation)]
    if not tokens:
        return 0.0, []
    duration = len(samples) / rate
    heard = expand_heard(words, pronunciation)
    timed = align_tokens([t for t, _ in tokens], heard, duration)
    score = coverage([t for t, _ in tokens], heard)
    cuts: list[float] = []
    for si in range(1, len(group.sentences)):
        ends = [timed[k][1] for k, (_, s) in enumerate(tokens) if s == si - 1]
        starts = [timed[k][0] for k, (_, s) in enumerate(tokens) if s == si]
        if not ends or not starts:
            return 0.0, []
        lo, hi = max(ends), min(starts)
        cuts.append(quietest(samples, rate, lo, hi) if hi > lo else (lo + hi) / 2)
    bounds = [0.0, *cuts, duration]
    if any(b - a < MIN_PIECE_S for a, b in zip(bounds, bounds[1:])):
        return 0.0, []
    return score, cuts


def quietest(samples: np.ndarray, rate: int, lo: float, hi: float, frame_s: float = 0.01) -> float:
    """Centre of the lowest-energy frame within [lo, hi] seconds."""
    frame = max(1, int(rate * frame_s))
    a, b = int(lo * rate), min(int(hi * rate), len(samples))
    if b - a < frame:
        return (lo + hi) / 2
    seg = samples[a:b].astype(np.float64)
    n = len(seg) // frame
    energy = (seg[: n * frame].reshape(n, frame) ** 2).mean(axis=1)
    return (a + int(np.argmin(energy)) * frame + frame // 2) / rate


def run_tts(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    try:
        provider = get_provider(env.settings, env.brand)
    except TTSError as e:
        raise StepError("tts", str(e)) from e
    health = provider.health()
    if not health.get("ok"):
        raise StepError("tts", health.get("message", "Voice Studio không sẵn sàng."))

    cfg, a = env.brand.tts, env.brand.audio
    pronunciation = load_pronunciation(env.settings.config_dir)
    groups, warnings = plan_groups(env.script, cfg.voices, env.settings.voice_studio_voice_id,
                                   pronunciation, cfg.group_max_chars)
    cache = cache_dir(env)
    cache.mkdir(parents=True, exist_ok=True)
    out_dir = tts_dir(env)
    out_dir.mkdir(parents=True, exist_ok=True)
    afilter = (f"{trim_filter(a.trim_silence_db, EDGE_KEEP_S, EDGE_KEEP_S)},"
               f"{normalize_format(a.sample_rate)}")

    timer: WhisperTimer | None = None
    whisper_lock = threading.Lock()
    stats = {"api_calls": 0, "retries": 0}
    stats_lock = threading.Lock()
    base_seed = cfg.seed if cfg.seed is not None else 42

    def check(group: Group, wav: Path, gkey: str) -> tuple[float, list[float]]:
        nonlocal timer
        check_file = cache / f"{gkey}.check.json"
        if check_file.exists():
            words = [WhisperWord(**w) for w in
                     json.loads(check_file.read_text(encoding="utf-8"))["words"]]
        else:
            with whisper_lock:
                if timer is None:
                    timer = WhisperTimer(env.settings.whisper_model, env.settings.whisper_device)
                # no prompt: a prompt makes whisper "hear" words the voice skipped
                words = timer.words(wav, language=cfg.language)
            write_json(check_file, {"words": [w.__dict__ for w in words]})
        samples = read_wav_mono16(wav, a.sample_rate)
        return score_and_cuts(group, words, samples, a.sample_rate, pronunciation)

    def take(group: Group, seed: int | None) -> Take:
        requests = [TTSRequest(text=chunk, voice_id=group.voice_id, language=cfg.language,
                               speed=cfg.speed, seed=seed, options=dict(cfg.sampling))
                    for chunk in chunk_text(group.speech_text, cfg.max_chars_per_request)]
        keys = [provider.cache_key(r) for r in requests]
        for key, req in zip(keys, requests):
            if not (cache / f"{key}.wav").exists():
                result = provider.synthesize(req)
                tmp = cache / f"{key}.{result.format}.tmp"
                tmp.write_bytes(result.audio)
                tmp.replace(cache / f"{key}.{result.format}")
                with stats_lock:
                    stats["api_calls"] += 1
        gkey = hashlib.sha256(json.dumps([keys, afilter]).encode()).hexdigest()
        wav = cache / f"{gkey}.48k.wav"
        if not wav.exists():
            inputs: list[str] = []
            for key in keys:
                inputs += ["-i", str(cache / f"{key}.wav")]
            n = len(keys)
            graph = "".join(f"[{k}:a]" for k in range(n)) + f"concat=n={n}:v=0:a=1,{afilter}"
            tmp = wav.with_suffix(".tmp.wav")
            run_ffmpeg([*inputs, "-filter_complex", graph, "-c:a", "pcm_s16le", str(tmp)],
                       "tts", f"chuẩn hóa audio câu {group.sentences[0].id}")
            tmp.replace(wav)
        score, cuts = check(group, wav, gkey)
        return Take(seed=seed, keys=keys, wav=wav, score=score, cuts=cuts)

    def realize(group: Group) -> Take:
        best: Take | None = None
        for attempt in range(max(1, cfg.seed_attempts)):
            if attempt:
                with stats_lock:
                    stats["retries"] += 1
            t = take(group, base_seed + attempt)
            if best is None or t.score > best.score:
                best = t
            if t.score >= cfg.min_word_match:
                break
        assert best is not None
        return best

    results: dict[int, Take] = {}
    pending = list(enumerate(groups))
    final: list[tuple[Group, Take]] = []
    fallbacks = 0
    done = 0
    total = len(groups)
    progress(0.0, f"{sum(len(g.sentences) for g in groups)} câu, đọc thành {total} lượt.")
    while pending:
        results.clear()
        with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as pool:
            futures = {pool.submit(realize, g): (i, g) for i, g in pending}
            for fut in as_completed(futures):
                i, g = futures[fut]
                try:
                    results[i] = fut.result()
                except TTSError as e:
                    for other in futures:
                        other.cancel()
                    first = g.sentences[0]
                    raise StepError("tts", f"Câu ở dòng {first.line} "
                                           f"({first.text[:40]}…): {e}") from e
                done += 1
                progress(0.9 * min(done / max(total, 1), 1.0), f"Đã đọc {done}/{total} lượt.")
        retry: list[tuple[int, Group]] = []
        for i, g in pending:
            t = results[i]
            if t.score < cfg.min_word_match and len(g.sentences) > 1:
                # keeps skipping words as a group: read its sentences one by one
                fallbacks += 1
                total += len(g.sentences)
                retry += [(len(groups) + k, Group([s])) for k, s in enumerate(g.sentences)]
                groups += [Group([s]) for s in g.sentences]
                continue
            if t.score < cfg.min_word_match:
                s = g.sentences[0]
                warnings.append(f"Câu ở dòng {s.line} có thể bị đọc sót chữ "
                                f"(nghe được {t.score:.0%}): {s.text[:60]}")
            final.append((g, t))
        pending = retry

    # cut every take back into one WAV per sentence
    order = {s.id: n for n, s in enumerate(env.script.sentences)}
    final.sort(key=lambda gt: order[gt[0].sentences[0].id])
    manifest: dict[str, Any] = {"provider": provider.name, "sentences": {}}
    for g, t in final:
        samples = read_wav_mono16(t.wav, a.sample_rate)
        bounds = [0, *(int(round(c * a.sample_rate)) for c in t.cuts), len(samples)]
        for k, s in enumerate(g.sentences):
            piece = samples[bounds[k]:bounds[k + 1]]
            target = out_dir / f"{s.id}.wav"
            write_wav_mono16(target, piece, a.sample_rate)
            manifest["sentences"][s.id] = {
                "file": f"tts/{s.id}.wav", "duration": round(len(piece) / a.sample_rate, 3),
                "text": s.text, "speech_text": s.speech_text, "voice_id": s.voice_id,
                "keys": t.keys, "glued": k > 0, "group_size": len(g.sentences),
                "seed": t.seed, "word_match": round(t.score, 3),
            }
    progress(1.0)

    for f in out_dir.glob("s*.wav"):
        if f.stem not in manifest["sentences"]:
            f.unlink()
    write_json(manifest_path(env), manifest)
    scores = [info["word_match"] for info in manifest["sentences"].values()]
    return {"sentences": len(manifest["sentences"]), "takes": len(final),
            "api_calls": stats["api_calls"], "retries": stats["retries"],
            "fallback_groups": fallbacks, "min_word_match": min(scores) if scores else None,
            "warnings": warnings}
