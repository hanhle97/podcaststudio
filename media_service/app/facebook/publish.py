"""Post an episode to the Facebook Page after approval: the 16:9 video, then Reels.

Idempotent: publish/facebook.json records what is already up, so a retry only sends
what is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.episodes import write_json
from app.facebook.client import FacebookClient, FacebookError, TokenStore
from app.jobs import JobContext, StepError
from app.tiktok.publish import caption, shorts_to_publish

REEL_MIN_S, REEL_MAX_S = 3, 90


def client_from_settings(settings: Settings) -> FacebookClient:
    return FacebookClient(settings.facebook_app_id, settings.facebook_app_secret,
                          settings.facebook_redirect_uri,
                          TokenStore(settings.data_dir / "secrets" / "facebook_token.json"),
                          page_wanted=settings.facebook_page_id,
                          version=settings.facebook_graph_version)


def video_text(meta: dict[str, Any]) -> tuple[str, str]:
    """(title, description) of the long video post; description keeps the chapters."""
    title = meta.get("title") or ""
    return title, (meta.get("description") or title).strip()


def summary_text(page: str, results: dict[str, Any]) -> str:
    lines = [f"📘 Facebook ({page}):"]
    video = results.get("video")
    if video:
        lines.append(f"Video tập: {video['url']}")
    reels = results.get("reels", [])
    if reels:
        lines.append(f"Reels ({len(reels)}):")
        lines += [f"  {r['url']}" for r in reels]
    if not video and not reels:
        lines.append("không có gì để đăng")
    return "\n".join(lines)


def publish_episode(root: Path, client: FacebookClient, max_reels: int, post_video: bool,
                    report: Callable[[float, str], None]) -> dict[str, Any]:
    meta_file = root / "metadata.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    episode = root / "episode_16x9.mp4"
    reels = [s for s in shorts_to_publish(root, max_reels)
             if REEL_MIN_S <= s["duration"] <= REEL_MAX_S]
    if post_video and not episode.exists():
        raise StepError("facebook", "Chưa có episode_16x9.mp4 để đăng.")
    try:
        page = client.page_info().get("name") or client.token().page_name
    except FacebookError as e:
        raise StepError("facebook", str(e)) from e

    done_file = root / "publish" / "facebook.json"
    done: dict[str, Any] = json.loads(done_file.read_text(encoding="utf-8")) \
        if done_file.exists() else {}
    page_id = client.token().page_id
    if done.get("page_id") not in (None, page_id):
        done = {}  # posted to another page before: start over for this one
    done["page_id"], done["page"] = page_id, page
    steps = (1 if post_video else 0) + len(reels)
    step = 0

    def stage(msg: str) -> Callable[[str], None]:
        base = step / max(steps, 1)
        return lambda m: report(base + 0.5 / max(steps, 1), f"{msg}: {m}")

    try:
        if post_video and not done.get("video"):
            title, description = video_text(meta)
            report(0.0, "Đăng video tập lên fanpage…")
            done["video"] = client.publish_video(episode, title, description,
                                                 stage("Video tập"))
            write_json(done_file, done)
        step += 1 if post_video else 0
        posted = {r["name"]: r for r in done.get("reels", [])}
        for s in reels:
            if s["name"] not in posted:
                report(step / max(steps, 1), f"Đăng Reel {s['name']}…")
                res = client.publish_reel(s["file"], caption(s["title"], s["hook"],
                                                             meta.get("tags", [])),
                                          stage(s["name"]))
                posted[s["name"]] = {**res, "name": s["name"], "title": s["title"]}
                done["reels"] = list(posted.values())
                write_json(done_file, done)
            step += 1
    except FacebookError as e:
        write_json(done_file, done)
        raise StepError("facebook", str(e)) from e
    done["reels"] = list(posted.values())
    write_json(done_file, done)
    return {"page": page, "video": done.get("video"), "reels": done["reels"],
            "summary_text": summary_text(page, done)}


def run_publish_facebook(settings: Settings, root: Path, ctx: JobContext) -> dict[str, Any]:
    ctx.update(step="facebook", message="Kiểm tra fanpage Facebook.")
    return publish_episode(root, client_from_settings(settings), settings.facebook_max_reels,
                           settings.facebook_post_video,
                           lambda p, m: ctx.update(progress=p, message=m))
