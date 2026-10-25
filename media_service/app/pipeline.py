"""Pipeline steps with input fingerprints, so re-running skips work that is still valid.

Each step declares its inputs (files + config) and outputs. After a successful
run a stamp with the input fingerprint is written to .stamps/{step}.json. A step
is "fresh" when all outputs exist and the stamp matches the current inputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path
from typing import Any, Callable

from app.config import BrandConfig, Settings
from app.episodes import EpisodePaths, EpisodeStore, write_json
from app.jobs import JobContext, StepError
from app.script import Script, ScriptParseError, parse_script


@dataclass
class StepEnv:
    settings: Settings
    brand: BrandConfig
    store: EpisodeStore
    episode_id: str

    @cached_property
    def paths(self) -> EpisodePaths:
        return self.store.paths(self.episode_id)

    @property
    def script(self) -> Script:
        return self.store.load_script(self.episode_id)


class StepProgress:
    """Maps a step's own 0..1 progress into its slice of the whole job."""

    def __init__(self, ctx: JobContext, base: float, span: float):
        self.ctx, self.base, self.span = ctx, base, span

    def __call__(self, fraction: float, message: str | None = None) -> None:
        self.ctx.update(progress=self.base + self.span * max(0.0, min(1.0, fraction)),
                        message=message)


@dataclass
class Step:
    name: str
    label: str  # Vietnamese, used in error messages / Telegram
    outputs: Callable[[StepEnv], list[Path]]
    inputs: Callable[[StepEnv], list[Path | str]]
    run: Callable[[StepEnv, StepProgress], dict[str, Any] | None]


def fingerprint(items: list[Path | str]) -> str:
    h = hashlib.sha256()
    for item in items:
        if isinstance(item, Path):
            h.update(item.name.encode())
            if item.is_file():
                with item.open("rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
            else:
                h.update(b"<missing>")
        else:
            h.update(item.encode())
        h.update(b"\0")
    return h.hexdigest()


def _stamp_path(env: StepEnv, step: Step) -> Path:
    return env.paths.stamps / f"{step.name}.json"


def is_fresh(env: StepEnv, step: Step) -> bool:
    stamp = _stamp_path(env, step)
    if not stamp.exists() or not all(p.exists() for p in step.outputs(env)):
        return False
    saved = json.loads(stamp.read_text(encoding="utf-8")).get("fingerprint")
    return saved == fingerprint(step.inputs(env))


def run_steps(env: StepEnv, ctx: JobContext, steps: list[Step],
              force: bool = False) -> dict[str, Any]:
    report: list[dict[str, Any]] = []
    n = len(steps)
    for i, step in enumerate(steps):
        ctx.update(step=step.name, progress=i / n)
        if not force and is_fresh(env, step):
            ctx.update(message=f"{step.label}: đã có kết quả hợp lệ, bỏ qua.")
            report.append({"step": step.name, "status": "skipped"})
            continue
        ctx.update(message=f"{step.label}: bắt đầu.")
        fp_inputs = step.inputs(env)  # fingerprint inputs as they were when we started
        result = step.run(env, StepProgress(ctx, i / n, 1 / n))
        missing = [p.name for p in step.outputs(env) if not p.exists()]
        if missing:
            raise StepError(step.name, f"{step.label}: thiếu file đầu ra {', '.join(missing)}.")
        write_json(_stamp_path(env, step), {
            "fingerprint": fingerprint(fp_inputs),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        ctx.update(message=f"{step.label}: xong.")
        report.append({"step": step.name, "status": "ran", **(result or {})})
    return {"episode_id": env.episode_id, "steps": report}


# --- steps -------------------------------------------------------------------------


def _parse_run(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    text = env.paths.script_md.read_text(encoding="utf-8")
    try:
        script = parse_script(text, env.brand.script)
    except ScriptParseError as e:
        raise StepError("parse", e.issues[0].__str__().splitlines()[0], e.summary()) from e
    env.paths.script_json.write_text(script.model_dump_json(indent=2), encoding="utf-8")
    return {"sentences": len(script.sentences), "chapters": len(script.chapters)}


PARSE = Step(
    name="parse",
    label="Đọc kịch bản",
    outputs=lambda env: [env.paths.script_json],
    inputs=lambda env: [env.paths.script_md, env.brand.script.model_dump_json()],
    run=_parse_run,
)

def _tts_run(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    from app.tts.synthesize import run_tts

    return run_tts(env, progress)


TTS = Step(
    name="tts",
    label="Tạo giọng đọc (Voice Studio)",
    outputs=lambda env: [env.paths.root / "tts" / "manifest.json"],
    inputs=lambda env: [
        env.paths.script_json, env.settings.config_dir / "pronunciation.yaml",
        env.brand.tts.model_dump_json(), env.settings.voice_studio_voice_id,
        str(env.brand.audio.sample_rate), str(env.brand.audio.trim_silence_db),
    ],
    run=_tts_run,
)


def _mix_run(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    from app.audio.mix import run_mix

    return run_mix(env, progress)


def _mix_inputs(env: StepEnv) -> list[Path | str]:
    from app.audio.mix import music_inputs

    return [env.paths.script_json, env.paths.root / "tts" / "manifest.json",
            env.brand.audio.model_dump_json(), *music_inputs(env)]


MIX = Step(
    name="mix",
    label="Ghép thoại và xử lý audio",
    outputs=lambda env: [env.paths.root / n for n in
                         ("segments.json", "final_audio.wav", "final_audio.mp3")],
    inputs=lambda env: _mix_inputs(env),
    run=_mix_run,
)

def _align_run(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    from app.align.step import run_align

    return run_align(env, progress)


ALIGN = Step(
    name="align",
    label="Lấy timing từng từ (faster-whisper)",
    outputs=lambda env: [env.paths.root / "words.json"],
    inputs=lambda env: [
        env.paths.root / "segments.json", env.paths.root / "tts" / "manifest.json",
        env.settings.config_dir / "pronunciation.yaml", env.settings.whisper_model,
    ],
    run=_align_run,
)


def _subtitles_run(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    from app.subtitles.step import run_subtitles

    return run_subtitles(env, progress)


SUBTITLES = Step(
    name="subtitles",
    label="Tạo phụ đề",
    outputs=lambda env: [env.paths.root / n for n in
                         ("subtitles.srt", "subtitles.ass", "karaoke.json")],
    inputs=lambda env: [
        env.paths.root / "words.json", env.brand.subtitles.model_dump_json(),
        env.brand.colors.model_dump_json(), env.brand.fonts.family,
    ],
    run=_subtitles_run,
)

# Order of the full pipeline; later phases append metadata, render, ...
PIPELINE: list[Step] = [PARSE, TTS, MIX, ALIGN, SUBTITLES]
STEPS: dict[str, Step] = {s.name: s for s in PIPELINE}
