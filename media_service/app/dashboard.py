"""API behind the web UI (/ui): status, script check/upload, episode overview, approvals.

n8n reports the Approve/Reject resume links of a draft (POST /episodes/{id}/approval)
and the outcome after publishing (POST /episodes/{id}/publish-status), so the UI can
show "waiting for approval" / "published" and approve without Telegram.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.config import get_settings
from app.episodes import EpisodePaths, slugify, write_json
from app.jobs import Job
from app.pipeline import STEPS
from app.script import parse_script
from app.summary import build_summary
from app.tiktok import publish as tiktok_publish
from app.tiktok.client import TikTokError
from app.tts import TTSError, get_provider

router = APIRouter()
settings = get_settings()
WORDS_PER_SECOND = 4.8  # measured pace of the cloned voice


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def publish_dir(paths: EpisodePaths) -> Path:
    return paths.root / "publish"


# --- derived state (pure, unit tested) -------------------------------------------------


def episode_state(*, has_video: bool, job: dict[str, Any] | None,
                  approval: dict[str, Any] | None, published: dict[str, Any] | None,
                  now: datetime) -> dict[str, Any]:
    """One status for the list: processing > failed > waiting > published/rejected > ready."""
    if job and job["status"] in ("queued", "running"):
        return {"state": "processing", "label": "Đang xử lý",
                "detail": job.get("step_label") or job.get("step") or "",
                "progress": job.get("progress", 0)}
    if job and job["status"] == "failed" and not (published and published.get("at", "")
                                                  > (job.get("finished_at") or "")):
        err = job.get("error") or {}
        return {"state": "failed", "label": "Lỗi",
                "detail": f"{job.get('step_label') or err.get('step') or ''}: "
                          f"{err.get('message', '')}".strip(": ")}
    if approval and approval.get("status") == "pending" and \
            datetime.fromisoformat(approval["expires_at"]) > now and \
            not (published and published.get("at", "") > approval.get("created_at", "")):
        return {"state": "waiting", "label": "Chờ duyệt", "detail": ""}
    if published:
        if published.get("state") == "published":
            return {"state": "published", "label": "Đã đăng", "detail": ""}
        return {"state": "rejected", "label": "Đã từ chối" if published.get("state") ==
                "rejected" else "Hết hạn duyệt", "detail": ""}
    if has_video:
        return {"state": "ready", "label": "Có video", "detail": ""}
    return {"state": "new", "label": "Mới tạo", "detail": ""}


def internal_url(url: str, base: str) -> str:
    """Point a resume link at n8n's internal address (the public host may be a LAN IP)."""
    parts, inner = urlsplit(url), urlsplit(base)
    return urlunsplit((inner.scheme, inner.netloc, parts.path, parts.query, ""))


def inbox_name(filename: str | None, existing: set[str]) -> str:
    stem = slugify(Path(filename or "kich-ban").stem, max_len=60)
    name, n = f"{stem}.md", 2
    while name in existing:
        name, n = f"{stem}-{n}.md", n + 1
    return name


# --- helpers ---------------------------------------------------------------------------


def _job_dict(job: Job | None) -> dict[str, Any] | None:
    if not job:
        return None
    data = job.model_dump(mode="json")
    step = STEPS.get(job.step or "")
    data["step_label"] = step.label if step else ("Đăng TikTok" if job.step == "tiktok"
                                                  else job.step)
    return data


def _episode_view(request: Request, episode_id: str) -> dict[str, Any]:
    st = request.app.state.store
    info = st.get(episode_id)
    paths = st.paths(episode_id)
    meta = _read(paths.root / "metadata.json") or {}
    segments = _read(paths.root / "segments.json") or {}
    jobs = request.app.state.jobs.list(episode_id, limit=1)
    approval = _read(publish_dir(paths) / "approval.json")
    published = _read(publish_dir(paths) / "status.json")
    base = f"/episodes/{episode_id}/files"
    return {
        "id": episode_id,
        "title": meta.get("title") or info.title or episode_id,
        "created_at": info.created_at.isoformat(),
        "duration": segments.get("duration"),
        "thumbnail": f"{base}/thumbnail.png" if (paths.root / "thumbnail.png").exists()
        else None,
        "status": episode_state(has_video=(paths.root / "episode_16x9.mp4").exists(),
                                job=_job_dict(jobs[0]) if jobs else None, approval=approval,
                                published=published, now=_now()),
    }


# --- system status -----------------------------------------------------------------------


@router.get("/ui/status")
async def system_status(request: Request) -> dict[str, Any]:
    async def voice() -> dict[str, Any]:
        try:
            info = await asyncio.wait_for(asyncio.to_thread(
                get_provider(settings, request.app.state.brand).health), 8)
        except (TTSError, asyncio.TimeoutError) as e:
            info = {"ok": False, "message": str(e) or "Voice Studio không phản hồi"}
        return {"ok": bool(info.get("ok")), "message": info.get("message", "")}

    async def n8n() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(f"{settings.n8n_internal_url}/healthz")
            return {"ok": r.status_code == 200, "message": "n8n đang chạy"
                    if r.status_code == 200 else f"n8n trả HTTP {r.status_code}"}
        except httpx.HTTPError:
            return {"ok": False, "message": "Không gọi được n8n"}

    async def tiktok() -> dict[str, Any]:
        client = tiktok_publish.client_from_settings(settings)
        if not client.configured():
            return {"ok": False, "optional": True, "message": "Chưa cấu hình (tùy chọn)"}
        if client.missing_scope() or not client.store.load():
            return {"ok": False, "optional": True, "message": "Chưa kết nối tài khoản"}
        try:
            user = await asyncio.wait_for(asyncio.to_thread(client.user_info), 8)
            return {"ok": True, "message": f"{user.get('display_name', '')} "
                                           f"({client.mode})".strip()}
        except (TikTokError, asyncio.TimeoutError, httpx.HTTPError) as e:
            return {"ok": False, "optional": True, "message": str(e) or "Lỗi kết nối TikTok"}

    llm = {"ok": bool(settings.claude_code_oauth_token or
                      os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
           "message": f"Claude Code ({settings.llm_model})"}
    if not llm["ok"]:
        llm["message"] = "Chưa có CLAUDE_CODE_OAUTH_TOKEN"
    v, n, t = await asyncio.gather(voice(), n8n(), tiktok())
    return {"voice_studio": v, "claude": llm, "n8n": n, "tiktok": t}


# --- scripts -----------------------------------------------------------------------------


class ScriptText(BaseModel):
    text: str


@router.post("/scripts/validate")
async def validate_script(request: Request, body: ScriptText) -> dict[str, Any]:
    """Parse without creating an episode; errors come back as 422 with line numbers."""
    brand = request.app.state.brand
    script = parse_script(body.text, brand.script)
    request.app.state.store._validate_assets(script, body.text)
    words = len(script.full_text().split())
    return {
        "title": script.meta.title, "has_headings": script.has_headings,
        "chapters": [c.title for c in script.chapters], "sentences": len(script.sentences),
        "speakers": script.speakers, "estimated_seconds": round(words / WORDS_PER_SECOND),
        "warnings": [w.model_dump() for w in script.warnings],
    }


@router.post("/inbox/upload", status_code=201)
async def upload_to_inbox(request: Request, file: UploadFile | None = File(None),
                          text: str | None = Form(None),
                          filename: str | None = Form(None)) -> dict[str, Any]:
    """Validate, then drop the script into data/inbox so the n8n workflow picks it up."""
    if file is not None:
        content, name = (await file.read()).decode("utf-8", errors="strict"), file.filename
    elif text is not None:
        content, name = text, filename
    else:
        raise HTTPException(400, "Gửi file kịch bản hoặc nội dung.")
    await validate_script(request, ScriptText(text=content))  # 422 with line errors
    inbox = settings.data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    target = inbox / inbox_name(name, {p.name for p in inbox.glob("*.md")})
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(target)
    return {"inbox_file": target.name}


# --- episodes ----------------------------------------------------------------------------


@router.get("/episodes/overview")
async def episodes_overview(request: Request) -> list[dict[str, Any]]:
    return [_episode_view(request, i.id) for i in request.app.state.store.list()]


@router.get("/episodes/{episode_id}/details")
async def episode_details(request: Request, episode_id: str) -> dict[str, Any]:
    view = _episode_view(request, episode_id)
    paths = request.app.state.store.paths(episode_id)
    jm = request.app.state.jobs
    return {
        **view,
        "summary": build_summary(paths.root, episode_id, ""),
        "metadata": _read(paths.root / "metadata.json"),
        "approval": _read(publish_dir(paths) / "approval.json"),
        "published": _read(publish_dir(paths) / "status.json"),
        "tiktok": _read(publish_dir(paths) / "tiktok.json"),
        "jobs": [_job_dict(j) for j in jm.list(episode_id, limit=10)],
        "files": request.app.state.store.files(episode_id),
        "steps": [{"name": s.name, "label": s.label} for s in STEPS.values()],
    }


# --- approvals (written by n8n, decided from the UI) -------------------------------------


class ApprovalLinks(BaseModel):
    approve_url: str
    reject_url: str
    hours: float = 48


class Decision(BaseModel):
    decision: Literal["approve", "reject"]


class PublishStatus(BaseModel):
    state: Literal["published", "rejected", "expired"]
    youtube_id: str | None = None
    short_ids: list[str] = []
    tiktok: str | None = None


@router.post("/episodes/{episode_id}/approval")
async def save_approval(request: Request, episode_id: str, body: ApprovalLinks) -> dict:
    paths = request.app.state.store.paths(episode_id)
    request.app.state.store.get(episode_id)
    now = _now()
    record = {"status": "pending", "approve_url": body.approve_url,
              "reject_url": body.reject_url, "created_at": now.isoformat(),
              "expires_at": (now + timedelta(hours=body.hours)).isoformat()}
    write_json(publish_dir(paths) / "approval.json", record)
    return {"ok": True}


@router.post("/episodes/{episode_id}/approval/decide")
async def decide(request: Request, episode_id: str, body: Decision) -> dict:
    paths = request.app.state.store.paths(episode_id)
    file = publish_dir(paths) / "approval.json"
    approval = _read(file)
    if not approval or approval.get("status") != "pending":
        raise HTTPException(409, "Tập này không có bản nháp nào đang chờ duyệt.")
    if datetime.fromisoformat(approval["expires_at"]) <= _now():
        raise HTTPException(409, "Bản nháp đã hết hạn duyệt (48 giờ).")
    url = approval["approve_url" if body.decision == "approve" else "reject_url"]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(internal_url(url, settings.n8n_internal_url))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Không gọi được n8n: {e}") from e
    if r.status_code >= 400:
        approval["status"] = "stale"
        write_json(file, approval)
        raise HTTPException(409, "n8n không còn chờ bản nháp này (có thể đã duyệt qua "
                                 "Telegram).")
    approval.update(status="decided", decision=body.decision, decided_at=_now().isoformat())
    write_json(file, approval)
    return {"ok": True, "decision": body.decision}


@router.post("/episodes/{episode_id}/publish-status")
async def save_publish_status(request: Request, episode_id: str, body: PublishStatus) -> dict:
    paths = request.app.state.store.paths(episode_id)
    request.app.state.store.get(episode_id)
    write_json(publish_dir(paths) / "status.json", {**body.model_dump(), "at": _now().isoformat()})
    approval_file = publish_dir(paths) / "approval.json"
    approval = _read(approval_file)
    if approval and approval.get("status") == "pending":
        approval.update(status="decided", decision="approve" if body.state == "published"
                        else "reject", decided_at=_now().isoformat())
        write_json(approval_file, approval)
    return {"ok": True}

