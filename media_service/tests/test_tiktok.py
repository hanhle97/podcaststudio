import json
import urllib.parse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.jobs import StepError
from app.main import app
from app.tiktok.client import (
    MIN_CHUNK, TikTokClient, TikTokError, Token, TokenStore, chunk_plan, chunk_ranges,
)
from app.tiktok.publish import caption, hashtag, publish_episode

MB = 1024 * 1024


def test_chunk_plan_rules():
    assert chunk_plan(3 * MB) == (3 * MB, 1)                  # under 5 MB: whole file
    assert chunk_plan(12 * MB) == (10 * MB, 1)                # last chunk absorbs the rest
    size = 50_000_123
    chunk, count = chunk_plan(size)
    assert (chunk, count) == (10 * MB, 4)
    ranges = chunk_ranges(size, chunk, count)
    assert ranges[0] == (0, 10 * MB - 1) and ranges[-1][1] == size - 1
    assert sum(b - a + 1 for a, b in ranges) == size
    assert all(b - a + 1 >= MIN_CHUNK for a, b in ranges)
    with pytest.raises(TikTokError):
        chunk_plan(0)


def test_hashtags_and_caption():
    assert hashtag("lập trình với AI") == "#laptrinhvoiAI"
    assert hashtag("Đà Nẵng") == "#DaNang" and hashtag("!!") == ""
    text = caption("Codex là gì?", "Agent thì tự đi sửa.",
                   ["Codex", "AI coding agent", "codex", "a", "b", "c", "d"])
    assert text.startswith("Codex là gì?\nAgent thì tự đi sửa.\n\n#Codex #AIcodingagent")
    assert len(text.split("\n\n")[1].split()) == 5


class FakeTikTok:
    """Minimal in-memory TikTok API for MockTransport."""

    def __init__(self, statuses=("PROCESSING_UPLOAD", "PUBLISH_COMPLETE"), privacy=None):
        self.calls: list[tuple[str, str]] = []
        self.puts: list[str] = []
        self.statuses = list(statuses)
        self.privacy = privacy or ["SELF_ONLY"]
        self.init_body = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        path = req.url.path
        if path == "/v2/oauth/token/":
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            assert form["client_key"] == "ck" and form["client_secret"] == "cs"
            return httpx.Response(200, json={
                "access_token": f"at-{form['grant_type']}", "refresh_token": "rt",
                "open_id": "oid", "expires_in": 86400, "refresh_expires_in": 31536000,
                "scope": "user.info.basic,video.publish"})
        if path == "/v2/post/publish/creator_info/query/":
            return httpx.Response(200, json={"data": {
                "creator_username": "hanhle", "privacy_level_options": self.privacy,
                "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if path == "/v2/post/publish/video/init/":
            self.init_body = json.loads(req.content)
            return httpx.Response(200, json={"data": {"publish_id": "p1",
                                                      "upload_url": "https://up.test/u"},
                                             "error": {"code": "ok"}})
        if req.url.host == "up.test":
            self.puts.append(req.headers["content-range"])
            return httpx.Response(201)
        if path == "/v2/post/publish/status/fetch/":
            return httpx.Response(200, json={"data": {"status": self.statuses.pop(0)},
                                             "error": {"code": "ok"}})
        return httpx.Response(404)


def make_client(tmp_path, fake, now=1000.0):
    clock = {"t": now}
    client = TikTokClient("ck", "cs", "https://x.trycloudflare.com/tiktok/auth/callback",
                          TokenStore(tmp_path / "tok.json"), transport=httpx.MockTransport(fake),
                          clock=lambda: clock["t"], sleep=lambda s: None)
    return client, clock


def test_authorize_url_and_unconfigured(tmp_path):
    client, _ = make_client(tmp_path, FakeTikTok())
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(client.authorize_url("st")).query))
    assert q == {"client_key": "ck", "response_type": "code",
                 "scope": "user.info.basic,video.publish",
                 "redirect_uri": "https://x.trycloudflare.com/tiktok/auth/callback", "state": "st"}
    bare = TikTokClient("", "", "", TokenStore(tmp_path / "t.json"))
    with pytest.raises(TikTokError, match="TIKTOK_CLIENT_KEY"):
        bare.authorize_url("s")


def test_token_exchange_and_refresh(tmp_path):
    fake = FakeTikTok()
    client, clock = make_client(tmp_path, fake)
    with pytest.raises(TikTokError, match="Chưa kết nối"):
        client.access_token()
    client.exchange_code("abc%2A")
    assert client.access_token().access_token == "at-authorization_code"
    clock["t"] += 86400  # expired -> refresh
    tok = client.access_token()
    assert tok.access_token == "at-refresh_token" and tok.open_id == "oid"
    assert TokenStore(tmp_path / "tok.json").load().access_token == "at-refresh_token"
    clock["t"] += 400 * 86400
    with pytest.raises(TikTokError, match="365 ngày"):
        client.access_token()


def test_publish_video_chunks_and_status(tmp_path):
    fake = FakeTikTok()
    client, _ = make_client(tmp_path, fake)
    client.exchange_code("c")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\0" * (12 * MB))
    res = client.publish_video(video, "Tiêu đề", "SELF_ONLY")
    assert res == {"publish_id": "p1", "status": "PUBLISH_COMPLETE", "post_id": None}
    src = fake.init_body["source_info"]
    assert src == {"source": "FILE_UPLOAD", "video_size": 12 * MB, "chunk_size": 10 * MB,
                   "total_chunk_count": 1}
    assert fake.init_body["post_info"]["privacy_level"] == "SELF_ONLY"
    assert fake.puts == [f"bytes 0-{12 * MB - 1}/{12 * MB}"]


def test_publish_video_failed(tmp_path):
    client, _ = make_client(tmp_path, FakeTikTok(statuses=["FAILED"]))
    client.exchange_code("c")
    (tmp_path / "v.mp4").write_bytes(b"\0" * 1000)
    with pytest.raises(TikTokError, match="thất bại"):
        client.publish_video(tmp_path / "v.mp4", "t", "SELF_ONLY")


def episode_dir(tmp_path, clips=3):
    root = tmp_path / "ep"
    (root / "shorts").mkdir(parents=True)
    (root / "render").mkdir()
    rendered = []
    for n in range(1, clips + 1):
        (root / "shorts" / f"clip_{n:02d}_9x16.mp4").write_bytes(b"\0" * 1000)
        rendered.append({"start": 0, "end": 40, "title": f"Clip {n}", "hook": f"Hook {n}"})
    (root / "render" / "clips_rendered.json").write_text(json.dumps(rendered), encoding="utf-8")
    (root / "metadata.json").write_text(json.dumps({"tags": ["Codex", "AI"]}), encoding="utf-8")
    return root


def test_publish_episode_limits_and_is_idempotent(tmp_path):
    fake = FakeTikTok(statuses=["PUBLISH_COMPLETE"] * 10)
    client, _ = make_client(tmp_path, fake)
    client.exchange_code("c")
    root = episode_dir(tmp_path, clips=4)
    out = publish_episode(root, client, "SELF_ONLY", 3, lambda p, m: None)
    assert [p["title"] for p in out["posts"]] == ["Clip 1", "Clip 2", "Clip 3"]
    assert out["creator"] == "hanhle"
    assert set(json.loads((root / "publish" / "tiktok.json").read_text())) == {
        "clip_01_9x16.mp4", "clip_02_9x16.mp4", "clip_03_9x16.mp4"}
    inits = sum(1 for c in fake.calls if c[1] == "/v2/post/publish/video/init/")
    again = publish_episode(root, client, "SELF_ONLY", 3, lambda p, m: None)
    assert all(p.get("skipped") for p in again["posts"])
    assert sum(1 for c in fake.calls if c[1] == "/v2/post/publish/video/init/") == inits


def test_publish_episode_rejects_disallowed_privacy(tmp_path):
    client, _ = make_client(tmp_path, FakeTikTok(privacy=["SELF_ONLY"]))
    client.exchange_code("c")
    with pytest.raises(StepError, match="SELF_ONLY"):
        publish_episode(episode_dir(tmp_path), client, "PUBLIC_TO_EVERYONE", 3, lambda p, m: None)


def test_auth_endpoints():
    with TestClient(app) as c:
        r = c.get("/tiktok/auth/start", follow_redirects=False)
        assert r.status_code == 400 and "TIKTOK_CLIENT_KEY" in r.json()["detail"]
        bad = c.get("/tiktok/auth/callback", params={"code": "x", "state": "forged"})
        assert bad.status_code == 400 and "không hợp lệ" in bad.text
        denied = c.get("/tiktok/auth/callback", params={"error": "access_denied"})
        assert denied.status_code == 400
        status = c.get("/tiktok/status").json()
        assert status["configured"] is False and status["connected"] is False
