"""FastAPI entry point of the media service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings, load_brand
from app.episodes import EpisodeNotFound, EpisodeStore
from app.jobs import EpisodeBusyError, Job, JobManager
from app.pipeline import PIPELINE, STEPS, StepEnv, run_steps
from app.script import ScriptParseError

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
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            raise HTTPException(422, "File kịch bản phải được lưu dạng UTF-8.") from None
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
            force: bool, kind: str) -> dict:
    st = store(request)
    st.get(episode_id)  # 404 early
    steps = PIPELINE if step_names is None else [STEPS[n] for n in step_names]
    env = StepEnv(settings=settings, brand=request.app.state.brand, store=st,
                  episode_id=episode_id)
    job = jobs(request).submit(kind, lambda ctx: run_steps(env, ctx, steps, force),
                               episode_id=episode_id)
    return {"job_id": job.id, "status_url": f"/jobs/{job.id}"}


@app.post("/episodes/{episode_id}/pipeline/run", status_code=202)
async def pipeline_run(request: Request, episode_id: str, force: bool = False) -> dict:
    return _submit(request, episode_id, None, force, "pipeline")


# --- jobs --------------------------------------------------------------------------


@app.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str) -> Job:
    job = jobs(request).get(job_id)
    if not job:
        raise HTTPException(404, f"Không tìm thấy job '{job_id}'.")
    return job


@app.get("/jobs")
async def list_jobs(request: Request, episode_id: str | None = None) -> list[Job]:
    return jobs(request).list(episode_id)
