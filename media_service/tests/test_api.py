import time

import pytest
from fastapi.testclient import TestClient

from app.main import app

SCRIPT = "---\ntitle: Thử nghiệm\n---\n## Mở đầu\nXin chào. [pause 1s]\n\n## Kết\nTạm biệt.\n"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job timeout")


def test_create_episode_and_run_pipeline(client):
    r = client.post("/episodes", files={"file": ("script.md", SCRIPT.encode(), "text/markdown")})
    assert r.status_code == 201, r.text
    body = r.json()
    ep = body["episode"]["id"]
    assert ep.startswith("thu-nghiem-")
    assert body["summary"]["chapters"] == ["Mở đầu", "Kết"]

    # same content -> same episode (idempotent)
    again = client.post("/episodes", data={"script": SCRIPT}).json()
    assert again["episode"]["id"] == ep

    # No Voice Studio in unit tests: parse succeeds, the run stops at tts with a clear reason.
    job = wait_job(client, client.post(f"/episodes/{ep}/pipeline/run").json()["job_id"])
    assert job["status"] == "failed", job
    assert job["error"]["step"] == "tts" and "VOICE_STUDIO_URL" in job["error"]["message"]
    assert any("[parse] Đọc kịch bản: xong" in line for line in job["log"])
    job = wait_job(client, client.post(f"/episodes/{ep}/pipeline/run").json()["job_id"])
    assert any("[parse] Đọc kịch bản: đã có kết quả hợp lệ" in line for line in job["log"])

    files = [f["path"] for f in client.get(f"/episodes/{ep}/files").json()["files"]]
    assert {"script.md", "script.json", "episode.json"} <= set(files)
    assert client.get(f"/episodes/{ep}/files/script.md").text == SCRIPT
    assert client.get(f"/episodes/{ep}/files/../episode.json").status_code == 404


def test_invalid_script_returns_line_numbers(client):
    r = client.post("/episodes", data={"script": "Một.\n[pasue 2s]\n"})
    assert r.status_code == 422
    body = r.json()
    assert body["step"] == "parse" and body["errors"][0]["line"] == 2
    assert "Dòng 2" in body["detail"]


def test_missing_music_file(client):
    r = client.post("/episodes", data={"script": "---\nmusic: khong_co.mp3\n---\nA."})
    assert r.status_code == 422 and r.json()["errors"][0]["line"] == 2


def test_tts_health_unconfigured(client):
    r = client.get("/tts/health")
    assert r.status_code == 503 and "VOICE_STUDIO_URL" in r.json()["message"]


def test_unknown_episode_and_job(client):
    assert client.get("/episodes/nope").status_code == 404
    assert client.get("/jobs/nope").status_code == 404
    assert client.post("/episodes/../x/pipeline/run").status_code == 404
