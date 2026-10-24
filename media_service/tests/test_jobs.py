import asyncio

import pytest

from app.jobs import EpisodeBusyError, JobManager, JobStatus, StepError


async def test_sync_job_reports_progress_and_result(tmp_path):
    jm = JobManager(tmp_path)

    def work(ctx):
        ctx.update(step="tts", progress=0.5, message="nửa đường")
        return {"ok": 1}

    job = jm.submit("demo", work, episode_id="ep")
    done = await jm.wait(job.id)
    assert done.status == JobStatus.succeeded
    assert done.result == {"ok": 1} and done.progress == 1.0
    assert any("nửa đường" in line for line in done.log)
    assert (tmp_path / f"{job.id}.json").exists()


async def test_step_error_records_step(tmp_path):
    jm = JobManager(tmp_path)

    def work(ctx):
        raise StepError("tts", "Voice Studio không phản hồi")

    done = await jm.wait(jm.submit("demo", work).id)
    assert done.status == JobStatus.failed
    assert done.error.step == "tts" and "Voice Studio" in done.error.message


async def test_unexpected_error_uses_current_step(tmp_path):
    jm = JobManager(tmp_path)

    async def work(ctx):
        ctx.update(step="mix")
        raise ValueError("hỏng")

    done = await jm.wait(jm.submit("demo", work).id)
    assert done.error.step == "mix" and "ValueError" in done.error.message
    assert done.error.detail


async def test_one_active_job_per_episode(tmp_path):
    jm = JobManager(tmp_path)
    gate = asyncio.Event()

    async def work(ctx):
        await gate.wait()

    first = jm.submit("a", work, episode_id="ep")
    with pytest.raises(EpisodeBusyError):
        jm.submit("b", work, episode_id="ep")
    jm.submit("c", lambda ctx: None, episode_id="other")
    gate.set()
    await jm.wait(first.id)
    jm.submit("d", lambda ctx: None, episode_id="ep")


async def test_restart_marks_running_jobs_failed(tmp_path):
    jm = JobManager(tmp_path)
    gate = asyncio.Event()

    async def work(ctx):
        ctx.update(step="render")
        await gate.wait()

    job = jm.submit("a", work)
    await asyncio.sleep(0.05)
    reloaded = JobManager(tmp_path).get(job.id)
    assert reloaded.status == JobStatus.failed and reloaded.error.step == "render"
    gate.set()
    await jm.wait(job.id)
