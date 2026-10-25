"""Episode summary for notifications and publishing (Telegram draft, YouTube upload)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.llm.timing import fmt_ts

OUTPUTS = {
    "video": "episode_16x9.mp4",
    "audio": "final_audio.mp3",
    "thumbnail": "thumbnail.png",
    "srt": "subtitles.srt",
    "metadata": "metadata.json",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def build_summary(root: Path, episode_id: str, public_url: str) -> dict[str, Any]:
    base = f"{public_url.rstrip('/')}/episodes/{episode_id}/files"
    meta = _read_json(root / "metadata.json") or {}
    segments = _read_json(root / "segments.json") or {}
    shorts = sorted(p.name for p in (root / "shorts").glob("clip_*_9x16.mp4")) \
        if (root / "shorts").is_dir() else []
    missing = [name for name in OUTPUTS.values() if not (root / name).exists()]
    duration = segments.get("duration", 0.0)
    return {
        "episode_id": episode_id,
        "ready": not missing and bool(shorts),
        "missing": missing,
        "title": meta.get("title"),
        "description": meta.get("description", ""),
        "description_body": meta.get("description_body", ""),
        "tags": meta.get("tags", []),
        "tags_csv": ",".join(meta.get("tags", [])),
        "chapters_text": meta.get("chapters_text", ""),
        "thumbnail_texts": meta.get("thumbnail_texts", []),
        "duration": duration,
        "duration_text": fmt_ts(duration),
        "paths": {k: f"/data/episodes/{episode_id}/{v}" for k, v in OUTPUTS.items()},
        "urls": {k: f"{base}/{v}" for k, v in OUTPUTS.items()},
        "shorts": [{"name": s, "url": f"{base}/shorts/{s}"} for s in shorts],
    }
