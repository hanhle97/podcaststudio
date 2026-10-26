import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.facebook.client import FacebookClient, FacebookError, PageToken, TokenStore, pick_page
from app.facebook.publish import publish_episode, summary_text, video_text
from app.jobs import StepError
from app.main import app

G = "/v23.0"


class FakeGraph:
    def __init__(self, pages=None, statuses=("processing", "ready")):
        self.pages = pages if pages is not None else [
            {"id": "111", "name": "Trang Một", "access_token": "page-tok-1"},
            {"id": "222", "name": "Podcast Hanh", "access_token": "page-tok-2"}]
        self.statuses = list(statuses)
        self.calls: list[tuple[str, str, dict]] = []
        self.next_id = 900

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        q = dict(req.url.params)
        form = {}
        if req.method == "POST" and req.headers.get("content-type", "").startswith(
                "application/x-www-form-urlencoded"):
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        self.calls.append((req.method, f"{req.url.host}{path}", {**q, **form}))
        if path == f"{G}/oauth/access_token":
            if q.get("grant_type") == "fb_exchange_token":
                return httpx.Response(200, json={"access_token": "long-user"})
            return httpx.Response(200, json={"access_token": "short-user"})
        if path == f"{G}/me/accounts":
            return httpx.Response(200, json={"data": self.pages})
        if path.endswith("/videos") and req.url.host == "graph-video.facebook.com":
            self.next_id += 1
            return httpx.Response(200, json={"id": f"v{self.next_id}"})
        if path.endswith("/video_reels"):
            if form.get("upload_phase") == "start":
                self.next_id += 1
                vid = f"r{self.next_id}"
                return httpx.Response(200, json={
                    "video_id": vid,
                    "upload_url": f"https://rupload.facebook.com/video-upload/v23.0/{vid}"})
            return httpx.Response(200, json={"success": True})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["authorization"].startswith("OAuth ")
            assert req.headers["offset"] == "0"
            assert int(req.headers["file_size"]) == len(req.content)
            return httpx.Response(200, json={"success": True})
        if path.startswith(f"{G}/v") or path.startswith(f"{G}/r"):
            vid = path.rsplit("/", 1)[1]
            state = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"status": {"video_status": state},
                                             "permalink_url": f"/reel/{vid}"})
        if path in (f"{G}/111", f"{G}/222"):
            return httpx.Response(200, json={"id": path[-3:], "name": "Podcast Hanh",
                                             "link": "https://facebook.com/x"})
        return httpx.Response(404, json={"error": {"message": f"no route {path}", "code": 803}})


def make_client(tmp_path, fake, page=""):
    return FacebookClient("app", "secret", "http://localhost:8000/facebook/auth/callback",
                          TokenStore(tmp_path / "fb.json"), page_wanted=page,
                          transport=httpx.MockTransport(fake), sleep=lambda s: None)


def connect(tmp_path, fake, page="Podcast Hanh"):
    client = make_client(tmp_path, fake, page)
    client.exchange_code("code123")
    return client


def test_authorize_url_asks_for_page_permissions(tmp_path):
    url = make_client(tmp_path, FakeGraph()).authorize_url("st")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://www.facebook.com/v23.0/dialog/oauth?")
    assert q["state"] == ["st"] and "pages_manage_posts" in q["scope"][0]
    bare = FacebookClient("", "", "", TokenStore(tmp_path / "x.json"))
    with pytest.raises(FacebookError, match="FACEBOOK_APP_ID"):
        bare.authorize_url("st")


def test_exchange_code_stores_the_chosen_page_token(tmp_path):
    fake = FakeGraph()
    client = connect(tmp_path, fake)
    tok = TokenStore(tmp_path / "fb.json").load()
    assert (tok.page_id, tok.page_name, tok.access_token) == ("222", "Podcast Hanh", "page-tok-2")
    assert [p["name"] for p in tok.pages] == ["Trang Một", "Podcast Hanh"]
    exchange = [c for c in fake.calls if c[2].get("grant_type") == "fb_exchange_token"][0]
    assert exchange[2]["fb_exchange_token"] == "short-user"
    assert client.token().page_id == "222"


def test_pick_page_rules():
    pages = [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]
    assert pick_page(pages, "")["id"] == "1"
    assert pick_page(pages, "2")["name"] == "B" and pick_page(pages, "B")["id"] == "2"
    with pytest.raises(FacebookError, match="Không thấy fanpage 'C'"):
        pick_page(pages, "C")
    with pytest.raises(FacebookError, match="không quản lý fanpage"):
        pick_page([], "")


def test_reel_runs_three_phases_and_waits(tmp_path):
    fake = FakeGraph(statuses=["processing", "processing", "ready"])
    client = connect(tmp_path, fake)
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\1" * 2048)
    res = client.publish_reel(clip, "Mô tả #AI")
    assert res["status"] == "published"
    assert res["url"].startswith("https://www.facebook.com/reel/")
    phases = [c[2].get("upload_phase") for c in fake.calls if c[1].endswith("/video_reels")]
    assert phases == ["start", "finish"]
    finish = [c for c in fake.calls if c[2].get("upload_phase") == "finish"][0][2]
    assert finish["video_state"] == "PUBLISHED" and finish["access_token"] == "page-tok-2"


def test_graph_error_is_explained(tmp_path):
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "Invalid token", "code": 190}})

    client = make_client(tmp_path, handler)
    TokenStore(tmp_path / "fb.json").save(PageToken("1", "P", "t", []))
    with pytest.raises(FacebookError, match="kết nối lại fanpage"):
        client.page_info()


def episode_dir(tmp_path, durations=(40, 40, 40, 40)):
    root = tmp_path / "ep"
    (root / "shorts").mkdir(parents=True)
    (root / "render").mkdir()
    rendered = []
    for n, d in enumerate(durations, 1):
        (root / "shorts" / f"clip_{n:02d}_9x16.mp4").write_bytes(b"\0" * 1000)
        rendered.append({"start": 0, "end": d, "title": f"Clip {n}", "hook": f"Hook {n}"})
    (root / "render" / "clips_rendered.json").write_text(json.dumps(rendered), encoding="utf-8")
    (root / "episode_16x9.mp4").write_bytes(b"\0" * 5000)
    (root / "metadata.json").write_text(json.dumps({
        "title": "Tập 1", "description": "Mô tả\n00:00 Mở đầu", "tags": ["AI"]}),
        encoding="utf-8")
    return root


def test_publish_episode_posts_video_and_reels_once(tmp_path):
    fake = FakeGraph()
    client = connect(tmp_path, fake)
    root = episode_dir(tmp_path, durations=(40, 120, 40, 40))  # the 120 s clip is no Reel
    res = publish_episode(root, client, 3, True, lambda p, m: None)
    assert res["video"]["video_id"].startswith("v")
    assert [r["name"] for r in res["reels"]] == ["clip_01_9x16.mp4", "clip_03_9x16.mp4"]
    assert len([c for c in fake.calls if c[1].endswith("/222/videos")]) == 1
    assert "Facebook (Podcast Hanh)" in res["summary_text"]

    before = len(fake.calls)
    again = publish_episode(root, client, 3, True, lambda p, m: None)
    assert [c for c in fake.calls[before:] if c[0] == "POST"] == []
    assert again["video"] == res["video"]


def test_publish_without_video_or_connection(tmp_path):
    client = connect(tmp_path, FakeGraph())
    res = publish_episode(episode_dir(tmp_path), client, 1, False, lambda p, m: None)
    assert res["video"] is None and len(res["reels"]) == 1
    other = tmp_path / "other"
    other.mkdir()
    fresh = make_client(other, FakeGraph())
    with pytest.raises(StepError, match="Chưa kết nối fanpage"):
        publish_episode(episode_dir(other), fresh, 3, True, lambda p, m: None)


def test_texts():
    assert video_text({"title": "T", "description": "D"}) == ("T", "D")
    assert video_text({"title": "T"}) == ("T", "T")
    assert summary_text("P", {}) == "📘 Facebook (P):\nkhông có gì để đăng"


def test_facebook_endpoints_without_config():
    with TestClient(app) as c:
        r = c.get("/facebook/auth/start", follow_redirects=False)
        assert r.status_code == 400 and "FACEBOOK_APP_ID" in r.json()["detail"]
        forged = c.get("/facebook/auth/callback", params={"code": "x", "state": "no"})
        assert forged.status_code == 400
        assert c.get("/facebook/auth/callback", params={"error": "denied"}).status_code == 400
        st = c.get("/facebook/status").json()
        assert st["configured"] is False and st["connected"] is False
