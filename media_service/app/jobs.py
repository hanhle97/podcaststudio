"""Async job queue with persisted status.

A job runs a (sync or async) function with a JobContext it uses to report the
current step and progress. Failures record which step failed so n8n can say
"Voice Studio không phản hồi (bước tts)" instead of a bare stack trace.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import traceback
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobError(BaseModel):
    step: str | None = None
    message: str
    detail: str | None = None


class Job(BaseModel):
    id: str
    kind: str
    episode_id: str | None = None
    status: JobStatus = JobStatus.queued
    step: str | None = None
    progress: float = 0.0  # 0..1, of the current job as a whole
    message: str | None = None
    error: JobError | None = None
    result: dict[str, Any] | None = None
    log: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in (JobStatus.queued, JobStatus.running)


class StepError(Exception):
    """Raise inside a job to fail it with a clear, user-facing message."""

    def __init__(self, step: str, message: str, detail: str | None = None):
        self.step = step
        self.message = message
        self.detail = detail
        super().__init__(f"[{step}] {message}")


class JobContext:
    """Handed to job functions; thread-safe so sync work in a thread can report."""

    def __init__(self, manager: JobManager, job: Job):
        self._manager = manager
        self.job = job

    def update(self, step: str | None = None, progress: float | None = None,
               message: str | None = None) -> None:
        with self._manager._lock:
            if step is not None:
                self.job.step = step
            if progress is not None:
                self.job.progress = max(0.0, min(1.0, progress))
            if message is not None:
                self.job.message = message
                stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self.job.log = (self.job.log + [f"{stamp} [{self.job.step}] {message}"])[-200:]
            self._manager._persist(self.job)


JobFn = Callable[[JobContext], Any | Awaitable[Any]]


class EpisodeBusyError(Exception):
    def __init__(self, job: Job):
        self.job = job
        super().__init__(f"Episode {job.episode_id} đang chạy job {job.id} ({job.kind}).")


class JobManager:
    def __init__(self, store_dir: Path, max_concurrent: int = 1):
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = threading.RLock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._load()

    # --- persistence -------------------------------------------------------------

    def _path(self, job_id: str) -> Path:
        return self.store_dir / f"{job_id}.json"

    def _persist(self, job: Job) -> None:
        tmp = self._path(job.id).with_suffix(".tmp")
        tmp.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self._path(job.id))

    def _load(self) -> None:
        for path in sorted(self.store_dir.glob("*.json")):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - a corrupt file must not stop startup
                log.warning("Bỏ qua file job hỏng: %s", path)
                continue
            if job.active:
                job.status = JobStatus.failed
                job.error = JobError(step=job.step, message="Media service khởi động lại "
                                     "trong khi job đang chạy. Hãy chạy lại.")
                job.finished_at = datetime.now(timezone.utc)
                self._persist(job)
            self._jobs[job.id] = job

    # --- API -----------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, episode_id: str | None = None, limit: int = 50) -> list[Job]:
        jobs = [j for j in self._jobs.values() if episode_id in (None, j.episode_id)]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    def active_for(self, episode_id: str) -> Job | None:
        return next((j for j in self._jobs.values()
                     if j.episode_id == episode_id and j.active), None)

    def submit(self, kind: str, fn: JobFn, episode_id: str | None = None) -> Job:
        """Queue `fn`; one active job per episode (raises EpisodeBusyError)."""
        with self._lock:
            if episode_id and (busy := self.active_for(episode_id)):
                raise EpisodeBusyError(busy)
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, episode_id=episode_id)
            self._jobs[job.id] = job
            self._persist(job)
        self._tasks[job.id] = asyncio.create_task(self._run(job, fn))
        return job

    async def wait(self, job_id: str) -> Job:
        task = self._tasks.get(job_id)
        if task:
            await task
        return self._jobs[job_id]

    async def _run(self, job: Job, fn: JobFn) -> None:
        ctx = JobContext(self, job)
        async with self._semaphore:
            with self._lock:
                job.status = JobStatus.running
                job.started_at = datetime.now(timezone.utc)
                self._persist(job)
            try:
                if inspect.iscoroutinefunction(fn):
                    result = await fn(ctx)
                else:
                    result = await asyncio.to_thread(fn, ctx)
                with self._lock:
                    job.status = JobStatus.succeeded
                    job.progress = 1.0
                    job.result = result if isinstance(result, dict) else None
            except StepError as e:
                log.warning("Job %s thất bại ở bước %s: %s", job.id, e.step, e.message)
                self._fail(job, JobError(step=e.step, message=e.message, detail=e.detail))
            except Exception as e:  # noqa: BLE001 - every failure must land in the job
                log.exception("Job %s lỗi không mong đợi", job.id)
                self._fail(job, JobError(step=job.step, message=f"{type(e).__name__}: {e}",
                                         detail=traceback.format_exc(limit=8)))
            finally:
                with self._lock:
                    job.finished_at = datetime.now(timezone.utc)
                    self._persist(job)
                self._tasks.pop(job.id, None)

    def _fail(self, job: Job, error: JobError) -> None:
        with self._lock:
            job.status = JobStatus.failed
            job.error = error
            if error.step:
                job.step = error.step
