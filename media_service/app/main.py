"""FastAPI entry point of the media service."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import inbox
from app.config import get_settings, load_brand
from app.episodes import EpisodeNotFound, EpisodeStore
from app.jobs import EpisodeBusyError, Job, JobManager
from app.pipeline import PIPELINE, STEPS, StepEnv, run_steps
from app.script import ScriptParseError
from app.summary import build_summary
from app.tiktok import publish as tiktok_publish
from app.tiktok.client import TikTokError
from app.tts import TTSError, get_provider

settings = get_settings()
logging.basicConfig(level=settings.log_level,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.brand = load_brand()
    app.state.store = EpisodeStore(settings, app.state.brand)
    app.state.jobs = JobManager(settings.jobs_dir, settings.max_concurrent_jobs)
    yield


app = FastAPI(title="Auto Podcast Studio – media service", lifespan=lifespan)


def store(request: Request) -> EpisodeStore:
    return request.app.state.store


def jobs(request: Request) -> JobManager:
    return request.app.state.jobs


@app.exception_handler(EpisodeNotFound)
async def _not_found(_: Request, exc: EpisodeNotFound):
    return JSONResponse(status_code=404,
                        content={"detail": f"Không tìm thấy episode '{exc.args[0]}'."})


@app.exception_handler(EpisodeBusyError)
async def _busy(_: Request, exc: EpisodeBusyError):
    return JSONResponse(status_code=409, content={
        "detail": str(exc), "job_id": exc.job.id, "job": exc.job.model_dump(mode="json")})


@app.exception_handler(ScriptParseError)
async def _script_error(_: Request, exc: ScriptParseError):
    return JSONResponse(status_code=422, content={
        "detail": exc.summary(),
        "step": "parse",
        "errors": [i.model_dump() for i in exc.issues],
    })


# --- health ------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/tts/health")
async def tts_health(request: Request) -> JSONResponse:
    """Checked by n8n before starting a pipeline."""
    try:
        provider = get_provider(settings, request.app.state.brand)
        info = await asyncio.to_thread(provider.health)
    except TTSError as e:
        info = {"ok": False, "message": str(e)}
    return JSONResponse(status_code=200 if info.get("ok") else 503, content=info)


# --- episodes ----------------------------------------------------------------------


@app.post("/episodes", status_code=201)
async def create_episode(
    request: Request,
    file: Annotated[UploadFile | None, File(description="script.md")] = None,
    script: Annotated[str | None, Form(description="Nội dung kịch bản (thay cho file)")] = None,
) -> dict:
    if file is not None:
        raw = await file.read()
        filename = file.filename
    elif script is not None:
        raw, filename = script.encode("utf-8"), None
    else:
        raise HTTPException(400, "Gửi kịch bản qua trường 'file' (multipart) hoặc 'script'.")
    return _create(request, _decode(raw), filename)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            raise HTTPException(422, "File kịch bản phải được lưu dạng UTF-8.") from None


def _create(request: Request, text: str, filename: str | None) -> dict:
    info, parsed = store(request).create(text, filename)
    return {
        "episode": info.model_dump(mode="json"),
        "summary": {
            "title": parsed.meta.title,
            "chapters": [c.title for c in parsed.chapters],
            "has_headings": parsed.has_headings,
            "sentences": len(parsed.sentences),
            "speakers": parsed.speakers,
        },
        "warnings": [str(w) for w in parsed.warnings],
    }


class InboxRequest(BaseModel):
    path: str = Field(description="Tên file hoặc đường dẫn /data/inbox/<file>.md")


class ArchiveRequest(InboxRequest):
    to: Literal["done", "rejected", "failed"] = "done"


@app.post("/inbox/episodes", status_code=201)
async def create_from_inbox(request: Request, body: InboxRequest) -> dict:
    """Create an episode from a script dropped in data/inbox (used by the n8n trigger)."""
    try:
        path = inbox.resolve(settings.data_dir, body.path)
    except inbox.InboxError as e:
        raise HTTPException(404, str(e)) from e
    return {**_create(request, _decode(path.read_bytes()), path.name), "inbox_file": path.name}


@app.post("/inbox/archive")
async def archive_inbox(body: ArchiveRequest) -> dict:
    try:
        dest = inbox.archive(settings.data_dir, body.path, body.to)
    except inbox.InboxError as e:
        raise HTTPException(404, str(e)) from e
    return {"moved_to": str(dest.relative_to(settings.data_dir).as_posix())}


# --- TikTok ------------------------------------------------------------------------


@app.get("/tiktok/status")
async def tiktok_status() -> dict:
    client = tiktok_publish.client_from_settings(settings)
    token = client.store.load()
    info: dict = {"configured": client.configured(), "connected": token is not None,
                  "mode": client.mode, "max_clips": settings.tiktok_max_clips}
    missing = client.missing_scope()
    if missing:
        return {**info, "connected": False,
                "error": f"token thiếu quyền {missing}, cần kết nối lại"}
    if token:
        try:
            if client.mode == "direct":
                creator = await asyncio.to_thread(client.creator_info)
                info.update(account=creator.get("creator_username"),
                            privacy=settings.tiktok_privacy,
                            privacy_options=creator.get("privacy_level_options"))
            else:
                user = await asyncio.to_thread(client.user_info)
                info.update(account=user.get("display_name"))
        except TikTokError as e:
            info.update(connected=False, error=str(e))
    return info


@app.get("/tiktok/auth/start")
async def tiktok_auth_start(request: Request) -> RedirectResponse:
    client = tiktok_publish.client_from_settings(settings)
    state = secrets.token_urlsafe(24)
    now = time.time()
    states = {k: v for k, v in getattr(request.app.state, "tiktok_states", {}).items() if v > now}
    states[state] = now + 600
    request.app.state.tiktok_states = states
    try:
        return RedirectResponse(client.authorize_url(state))
    except TikTokError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/tiktok/auth/callback", response_class=HTMLResponse)
async def tiktok_auth_callback(request: Request, code: str = "", state: str = "",
                               error: str = "", error_description: str = "") -> HTMLResponse:
    states = getattr(request.app.state, "tiktok_states", {})
    if error:
        return _page(f"TikTok từ chối: {error} {error_description}", ok=False)
    if not code or states.pop(state, 0) < time.time():
        return _page("Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Mở lại /tiktok/auth/start.",
                     ok=False)
    client = tiktok_publish.client_from_settings(settings)
    try:
        await asyncio.to_thread(client.exchange_code, code)
    except TikTokError as e:
        return _page(str(e), ok=False)
    return _page("Đã kết nối TikTok. Có thể đóng trang này và tắt tunnel.", ok=True)


def _page(message: str, ok: bool) -> HTMLResponse:
    color = "#15803d" if ok else "#b91c1c"
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;"
                        f"padding:40px'><h2 style='color:{color}'>{html.escape(message)}</h2>",
                        status_code=200 if ok else 400)


@app.post("/episodes/{episode_id}/publish/tiktok", status_code=202)
async def publish_tiktok(request: Request, episode_id: str,
                         max_clips: int | None = None) -> dict:
    store(request).get(episode_id)
    root = store(request).paths(episode_id).root
    n = max_clips or settings.tiktok_max_clips
    job = jobs(request).submit(
        "publish:tiktok", lambda ctx: tiktok_publish.run_publish_tiktok(settings, root, n, ctx),
        episode_id=episode_id)
    return {"job_id": job.id, "status_url": f"/jobs/{job.id}"}


@app.get("/episodes")
async def list_episodes(request: Request) -> list[dict]:
    return [i.model_dump(mode="json") for i in store(request).list()]


@app.get("/episodes/{episode_id}")
async def get_episode(request: Request, episode_id: str) -> dict:
    info = store(request).get(episode_id)
    return {
        "episode": info.model_dump(mode="json"),
        "jobs": [j.model_dump(mode="json") for j in jobs(request).list(episode_id, limit=10)],
    }


@app.get("/episodes/{episode_id}/summary")
async def episode_summary(request: Request, episode_id: str) -> dict:
    """Title, description, tags and public links of the outputs (Telegram / YouTube)."""
    st = store(request)
    st.get(episode_id)
    return build_summary(st.paths(episode_id).root, episode_id, settings.media_public_url)


@app.get("/episodes/{episode_id}/files")
async def list_files(request: Request, episode_id: str) -> dict:
    return {"episode_id": episode_id, "files": store(request).files(episode_id)}


@app.get("/episodes/{episode_id}/files/{path:path}")
async def get_file(request: Request, episode_id: str, path: str) -> FileResponse:
    root = store(request).paths(episode_id).root.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, f"Không có file '{path}'.")
    return FileResponse(target)


# --- pipeline ----------------------------------------------------------------------


def _submit(request: Request, episode_id: str, step_names: list[str] | None,
            force: bool, kind: str, options: dict | None = None) -> dict:
    st = store(request)
    st.get(episode_id)  # 404 early
    steps = PIPELINE if step_names is None else [STEPS[n] for n in step_names]
    env = StepEnv(settings=settings, brand=request.app.state.brand, store=st,
                  episode_id=episode_id, options=options or {})
    job = jobs(request).submit(kind, lambda ctx: run_steps(env, ctx, steps, force),
                               episode_id=episode_id)
    return {"job_id": job.id, "status_url": f"/jobs/{job.id}"}


@app.post("/episodes/{episode_id}/tts", status_code=202)
async def step_tts(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["tts"], True, "tts")


@app.post("/episodes/{episode_id}/mix", status_code=202)
async def step_mix(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["mix"], True, "mix")


@app.post("/episodes/{episode_id}/align", status_code=202)
async def step_align(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["align"], True, "align")


@app.post("/episodes/{episode_id}/subtitles", status_code=202)
async def step_subtitles(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["subtitles"], True, "subtitles")


@app.post("/episodes/{episode_id}/metadata", status_code=202)
async def step_metadata(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["metadata"], True, "metadata")


@app.post("/episodes/{episode_id}/clips/select", status_code=202)
async def step_clips(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["clips"], True, "clips")


@app.post("/episodes/{episode_id}/thumbnail", status_code=202)
async def step_thumbnail(request: Request, episode_id: str) -> dict:
    return _submit(request, episode_id, ["thumbnail"], True, "thumbnail")


@app.post("/episodes/{episode_id}/render", status_code=202)
async def step_render(request: Request, episode_id: str,
                      format: Literal["landscape", "short", "all"] = "all") -> dict:
    return _submit(request, episode_id, ["render"], True, f"render:{format}",
                   {"format": format})


@app.post("/episodes/{episode_id}/pipeline/run", status_code=202)
async def pipeline_run(request: Request, episode_id: str, force: bool = False) -> dict:
    return _submit(request, episode_id, None, force, "pipeline")


# --- jobs --------------------------------------------------------------------------


@app.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> dict:
    job = jobs(request).get(job_id)
    if not job:
        raise HTTPException(404, f"Không tìm thấy job '{job_id}'.")
    data = job.model_dump(mode="json")
    step = STEPS.get(job.step or "")
    data["step_label"] = step.label if step else job.step
    return data


@app.get("/jobs")
async def list_jobs(request: Request, episode_id: str | None = None) -> list[Job]:
    return jobs(request).list(episode_id)
