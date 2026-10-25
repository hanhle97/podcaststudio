"""Publish an episode's Shorts to TikTok (called after approval, not part of the pipeline)."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.episodes import write_json
from app.jobs import JobContext, StepError
from app.tiktok.client import TikTokClient, TikTokError, TokenStore


def client_from_settings(settings: Settings) -> TikTokClient:
    return TikTokClient(settings.tiktok_client_key, settings.tiktok_client_secret,
                        settings.tiktok_redirect_uri,
                        TokenStore(settings.data_dir / "secrets" / "tiktok_token.json"))


def hashtag(tag: str) -> str:
    """'lập trình với AI' -> '#laptrinhvoiAI' (TikTok hashtags have no spaces)."""
    text = tag.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]", "", text)
    return f"#{text}" if text else ""


def caption(title: str, hook: str, tags: list[str], limit: int = 5) -> str:
    tags_line = " ".join(dict.fromkeys(h for h in (hashtag(t) for t in tags) if h))
    tags_line = " ".join(tags_line.split()[:limit])
    body = title if not hook or hook in title else f"{title}\n{hook}"
    return f"{body}\n\n{tags_line}".strip()


def shorts_to_publish(root: Path, max_clips: int) -> list[dict[str, Any]]:
    clips_file = root / "render" / "clips_rendered.json"
    clips = json.loads(clips_file.read_text(encoding="utf-8")) if clips_file.exists() else []
    out = []
    for n, clip in enumerate(clips[:max_clips], 1):
        path = root / "shorts" / f"clip_{n:02d}_9x16.mp4"
        if path.exists():
            out.append({"file": path, "name": path.name, "title": clip.get("title") or "",
                        "hook": clip.get("hook") or "",
                        "duration": clip["end"] - clip["start"]})
    return out


def publish_episode(root: Path, client: TikTokClient, privacy: str, max_clips: int,
                    report: Callable[[float, str], None]) -> dict[str, Any]:
    meta_file = root / "metadata.json"
    tags = json.loads(meta_file.read_text(encoding="utf-8")).get("tags", []) \
        if meta_file.exists() else []
    shorts = shorts_to_publish(root, max_clips)
    if not shorts:
        raise StepError("tiktok", "Tập này chưa có Short nào để đăng.")
    try:
        info = client.creator_info()
    except TikTokError as e:
        raise StepError("tiktok", str(e)) from e
    options = info.get("privacy_level_options") or []
    if privacy not in options:
        raise StepError("tiktok", f"Tài khoản TikTok không cho chế độ '{privacy}'. "
                                  f"Được phép: {', '.join(options) or 'không rõ'}. "
                                  "App chưa qua audit chỉ đăng được SELF_ONLY.")
    max_dur = info.get("max_video_post_duration_sec") or 600

    done_file = root / "publish" / "tiktok.json"
    done: dict[str, Any] = json.loads(done_file.read_text(encoding="utf-8")) \
        if done_file.exists() else {}
    results = []
    for i, s in enumerate(shorts):
        base, span = i / len(shorts), 1 / len(shorts)
        if done.get(s["name"], {}).get("status") == "PUBLISH_COMPLETE":
            results.append({**done[s["name"]], "skipped": True})
            continue
        if s["duration"] > max_dur:
            raise StepError("tiktok", f"{s['name']} dài {s['duration']:.0f}s, vượt giới hạn "
                                      f"{max_dur}s của tài khoản TikTok.")
        report(base, f"Đăng {s['name']} lên TikTok…")
        try:
            res = client.publish_video(
                s["file"], caption(s["title"], s["hook"], tags), privacy,
                lambda msg, b=base, sp=span: report(b + sp * 0.5, f"{s['name']}: {msg}"))
        except TikTokError as e:
            write_json(done_file, done)
            raise StepError("tiktok", f"{s['name']}: {e}") from e
        done[s["name"]] = {**res, "title": s["title"], "privacy": privacy}
        write_json(done_file, done)
        results.append(done[s["name"]])
    return {"creator": info.get("creator_username"), "privacy": privacy, "posts": results}


def run_publish_tiktok(settings: Settings, root: Path, max_clips: int,
                       ctx: JobContext) -> dict[str, Any]:
    ctx.update(step="tiktok", message="Kiểm tra tài khoản TikTok.")
    client = client_from_settings(settings)
    return publish_episode(root, client, settings.tiktok_privacy, max_clips,
                           lambda p, m: ctx.update(progress=p, message=m))
