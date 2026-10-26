import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import dashboard
from app.config import get_settings
from app.dashboard import episode_state, inbox_name, internal_url
from app.main import app

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat()


def test_episode_state_priority():
    running = {"status": "running", "step": "tts", "step_label": "Tạo giọng", "progress": 0.4}
    assert episode_state(has_video=True, job=running, approval=None, published=None,
                         now=NOW)["state"] == "processing"
    failed = {"status": "failed", "step_label": "Render", "finished_at": iso(NOW),
              "error": {"step": "render", "message": "boom"}}
    st = episode_state(has_video=False, job=failed, approval=None, published=None, now=NOW)
    assert st["state"] == "failed" and "Render" in st["detail"] and "boom" in st["detail"]
    ok_job = {"status": "succeeded"}
    pending = {"status": "pending", "created_at": iso(NOW - timedelta(hours=1)),
               "expires_at": iso(NOW + timedelta(hours=47))}
    assert episode_state(has_video=True, job=ok_job, approval=pending, published=None,
                         now=NOW)["state"] == "waiting"
    expired = {**pending, "expires_at": iso(NOW - timedelta(minutes=1))}
    assert episode_state(has_video=True, job=ok_job, approval=expired, published=None,
                         now=NOW)["state"] == "ready"
    pub = {"state": "published", "at": iso(NOW)}
    assert episode_state(has_video=True, job=ok_job, approval=pending, published=pub,
                         now=NOW)["state"] == "published"
    assert episode_state(has_video=True, job=None, approval=None,
                         published={"state": "rejected", "at": iso(NOW)},
                         now=NOW)["label"] == "Đã từ chối"
    assert episode_state(has_video=False, job=None, approval=None, published=None,
                         now=NOW)["state"] == "new"


def test_internal_url_and_inbox_name():
    url = "http://192.168.100.144:5678/webhook-waiting/20?signature=abc&decision=approve"
    assert internal_url(url, "http://n8n:5678") == \
        "http://n8n:5678/webhook-waiting/20?signature=abc&decision=approve"
    assert inbox_name("Tập 12: n8n.md", set()) == "tap-12-n8n.md"
    assert inbox_name("x.md", {"x.md", "x-2.md"}) == "x-3.md"
    assert inbox_name(None, set()) == "kich-ban.md"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_root_and_ui(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"
    page = client.get("/ui/")
    assert page.status_code == 200 and "Podcast Studio" in page.text
    assert client.get("/ui/app.js").status_code == 200


def test_validate_script(client):
    ok = client.post("/scripts/validate", json={"text": "## Một\nXin chào các bạn.\n"})
    assert ok.status_code == 200
    assert ok.json()["chapters"] == ["Một"] and ok.json()["estimated_seconds"] >= 0
    bad = client.post("/scripts/validate", json={"text": "A.\n[pasue 2s]\n"})
    assert bad.status_code == 422 and bad.json()["errors"][0]["line"] == 2


def test_inbox_upload(client):
    inbox = get_settings().data_dir / "inbox"
    r = client.post("/inbox/upload", data={"text": "Xin chào.\n", "filename": "Tập thử.md"})
    assert r.status_code == 201 and r.json()["inbox_file"] == "tap-thu.md"
    assert (inbox / "tap-thu.md").read_text(encoding="utf-8") == "Xin chào.\n"
    again = client.post("/inbox/upload", data={"text": "Lần hai.\n", "filename": "Tập thử.md"})
    assert again.json()["inbox_file"] == "tap-thu-2.md"
    bad = client.post("/inbox/upload", data={"text": "[pasue 1s]", "filename": "x.md"})
    assert bad.status_code == 422 and not (inbox / "x.md").exists()


def test_overview_details_and_approval_flow(client, monkeypatch):
    ep = client.post("/episodes", data={"script": "## Mở đầu\nXin chào bảng điều khiển.\n"}
                     ).json()["episode"]["id"]
    overview = {e["id"]: e for e in client.get("/episodes/overview").json()}
    assert overview[ep]["status"]["state"] == "new"

    links = {"approve_url": "http://192.168.1.2:5678/webhook-waiting/9?signature=s&decision=approve",
             "reject_url": "http://192.168.1.2:5678/webhook-waiting/9?signature=s&decision=reject"}
    assert client.post(f"/episodes/{ep}/approval", json=links).status_code == 200
    details = client.get(f"/episodes/{ep}/details").json()
    assert details["status"]["state"] == "waiting" and details["approval"]["status"] == "pending"
    assert {s["name"] for s in details["steps"]} >= {"tts", "render"}

    called = {}

    async def fake_get(self, url, **kw):
        called["url"] = url
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    r = client.post(f"/episodes/{ep}/approval/decide", json={"decision": "approve"})
    assert r.status_code == 200
    assert called["url"].startswith("http://n8n:5678/webhook-waiting/9?") \
        and called["url"].endswith("decision=approve")
    assert client.post(f"/episodes/{ep}/approval/decide",
                       json={"decision": "approve"}).status_code == 409  # already decided

    r = client.post(f"/episodes/{ep}/publish-status",
                    json={"state": "published", "youtube_id": "abc123XYZ", "short_ids": ["s1"]})
    assert r.status_code == 200
    details = client.get(f"/episodes/{ep}/details").json()
    assert details["status"]["state"] == "published"
    assert details["published"]["youtube_id"] == "abc123XYZ"


def test_decide_when_n8n_no_longer_waits(client, monkeypatch):
    ep = client.post("/episodes", data={"script": "## A\nCâu chờ duyệt cũ.\n"}).json()["episode"]["id"]
    client.post(f"/episodes/{ep}/approval", json={"approve_url": "http://x/w/1?decision=approve",
                                                 "reject_url": "http://x/w/1?decision=reject"})

    async def gone(self, url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", gone)
    r = client.post(f"/episodes/{ep}/approval/decide", json={"decision": "reject"})
    assert r.status_code == 409 and "Telegram" in r.json()["detail"]
    approval = json.loads((get_settings().episodes_dir / ep / "publish" / "approval.json")
                          .read_text(encoding="utf-8"))
    assert approval["status"] == "stale"


def test_status_endpoint_shape(client, monkeypatch):
    async def down(self, url, **kw):
        raise httpx.ConnectError("no n8n")

    monkeypatch.setattr(httpx.AsyncClient, "get", down)
    s = client.get("/ui/status").json()
    assert set(s) == {"voice_studio", "claude", "n8n", "tiktok", "facebook"}
    assert s["n8n"]["ok"] is False and s["voice_studio"]["ok"] is False
    assert s["tiktok"]["optional"] is True and s["facebook"]["optional"] is True
    assert dashboard.WORDS_PER_SECOND > 0
