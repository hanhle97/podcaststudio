"""Send an episode's Shorts to TikTok (called after approval, not part of the pipeline).

inbox mode (default): each clip lands in the creator's TikTok inbox as a draft; the
creator adds the caption (we suggest one) and posts from the app.
direct mode: posts directly (needs an audited app or a private account).
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.episodes import write_json
from app.jobs import JobContext, StepError
from app.tiktok.client import DONE_STATUSES, TikTokClient, TikTokError, TokenStore


def client_from_settings(settings: Settings) -> TikTokClient:
    return TikTokClient(settings.tiktok_client_key, settings.tiktok_client_secret,
                        settings.tiktok_redirect_uri,
                        TokenStore(settings.data_dir / "secrets" / "tiktok_token.json"),
                        mode=settings.tiktok_mode)


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


def summary_text(mode: str, account: str | None, posts: list[dict[str, Any]],
                 privacy: str) -> str:
    """Telegram-ready summary; in inbox mode includes the caption to paste for each clip."""
    if mode == "direct":  # creator_username
        who = f" @{account}" if account else ""
        return f"🎵 TikTok{who}: đã đăng {len(posts)} video ({privacy})"
    who = f" ({account})" if account else ""  # display name, not a handle
    lines = [f"🎵 TikTok{who}: đã gửi {len(posts)} video vào hộp nháp.",
             "Mở app TikTok → Hộp thư → thông báo bản nháp → dán caption → Đăng."]
    for n, p in enumerate(posts, 1):
        lines.append(f"\n{n}. {p['name']}\n{p['caption']}")
    return "\n".join(lines)


def publish_episode(root: Path, client: TikTokClient, privacy: str, max_clips: int,
                    report: Callable[[float, str], None]) -> dict[str, Any]:
    meta_file = root / "metadata.json"
    tags = json.loads(meta_file.read_text(encoding="utf-8")).get("tags", []) \
        if meta_file.exists() else []
    shorts = shorts_to_publish(root, max_clips)
    if not shorts:
        raise StepError("tiktok", "Tập này chưa có Short nào để đăng.")
    missing = client.missing_scope()
    if missing:
        raise StepError("tiktok", f"Token TikTok thiếu quyền {missing}. Kết nối lại qua "
                                  "/tiktok/auth/start (xem README mục TikTok).")
    try:
        if client.mode == "direct":
            info = client.creator_info()
            account = info.get("creator_username")
            options = info.get("privacy_level_options") or []
            if privacy not in options:
                raise StepError("tiktok", f"Tài khoản TikTok không cho chế độ '{privacy}'. "
                                          f"Được phép: {', '.join(options) or 'không rõ'}.")
            max_dur = info.get("max_video_post_duration_sec") or 600
        else:
            account = client.user_info().get("display_name")
            max_dur = 600
    except TikTokError as e:
        raise StepError("tiktok", str(e)) from e

    done_file = root / "publish" / "tiktok.json"
    done: dict[str, Any] = json.loads(done_file.read_text(encoding="utf-8")) \
        if done_file.exists() else {}
    finished = DONE_STATUSES[client.mode]
    results = []
    for i, s in enumerate(shorts):
        base, span = i / len(shorts), 1 / len(shorts)
        text = caption(s["title"], s["hook"], tags)
        prev = done.get(s["name"], {})
        if prev.get("mode", client.mode) == client.mode and prev.get("status") in finished:
            results.append({**prev, "skipped": True})
            continue
        if s["duration"] > max_dur:
            raise StepError("tiktok", f"{s['name']} dài {s['duration']:.0f}s, vượt giới hạn "
                                      f"{max_dur}s của tài khoản TikTok.")
        report(base, f"Gửi {s['name']} lên TikTok…")
        progress = lambda msg, b=base, sp=span, n=s["name"]: report(b + sp * 0.5, f"{n}: {msg}")
        try:
            if client.mode == "direct":
                res = client.publish_video(s["file"], text, privacy, progress)
            else:
                res = client.upload_to_inbox(s["file"], progress)
        except TikTokError as e:
            write_json(done_file, done)
            raise StepError("tiktok", f"{s['name']}: {e}") from e
        done[s["name"]] = {**res, "name": s["name"], "title": s["title"], "caption": text,
                           "mode": client.mode, "privacy": privacy}
        write_json(done_file, done)
        results.append(done[s["name"]])
    return {"mode": client.mode, "account": account, "privacy": privacy, "posts": results,
            "summary_text": summary_text(client.mode, account, results, privacy)}


def run_publish_tiktok(settings: Settings, root: Path, max_clips: int,
                       ctx: JobContext) -> dict[str, Any]:
    ctx.update(step="tiktok", message="Kiểm tra tài khoản TikTok.")
    client = client_from_settings(settings)
    return publish_episode(root, client, settings.tiktok_privacy, max_clips,
                           lambda p, m: ctx.update(progress=p, message=m))
