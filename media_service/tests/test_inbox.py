import json

import pytest
from fastapi.testclient import TestClient

from app import inbox
from app.config import get_settings
from app.main import app
from app.summary import build_summary


def test_resolve_and_archive(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "tap1.md").write_text("Xin chào.", encoding="utf-8")
    assert inbox.resolve(tmp_path, "/data/inbox/tap1.md").name == "tap1.md"
    with pytest.raises(inbox.InboxError, match="Không tìm thấy"):
        inbox.resolve(tmp_path, "nope.md")
    with pytest.raises(inbox.InboxError, match="không nằm trực tiếp"):
        inbox.resolve(tmp_path, "../secret.md")

    first = inbox.archive(tmp_path, "tap1.md", "done")
    assert first == tmp_path / "inbox" / "done" / "tap1.md" and first.exists()
    (tmp_path / "inbox" / "tap1.md").write_text("Lần hai.", encoding="utf-8")
    second = inbox.archive(tmp_path, "tap1.md", "done")
    assert second != first and second.name.startswith("tap1-") and first.exists()


def test_summary(tmp_path):
    root = tmp_path / "ep"
    (root / "shorts").mkdir(parents=True)
    (root / "metadata.json").write_text(json.dumps({
        "title": "Tập 1", "description": "Mô tả\n\nMục lục:\n00:00 Mở đầu",
        "description_body": "Mô tả", "tags": ["n8n", "tự động hóa"],
        "chapters_text": "00:00 Mở đầu", "thumbnail_texts": ["a", "b", "c"]}), encoding="utf-8")
    (root / "segments.json").write_text(json.dumps({"duration": 75.4}), encoding="utf-8")
    for name in ("episode_16x9.mp4", "final_audio.mp3", "thumbnail.png", "subtitles.srt"):
        (root / name).write_bytes(b"x")
    s = build_summary(root, "ep", "http://192.168.1.5:8000/")
    assert not s["ready"] and s["missing"] == []  # no shorts yet
    (root / "shorts" / "clip_01_9x16.mp4").write_bytes(b"x")
    s = build_summary(root, "ep", "http://192.168.1.5:8000/")
    assert s["ready"] and s["duration_text"] == "01:15" and s["tags_csv"] == "n8n,tự động hóa"
    assert s["urls"]["video"] == "http://192.168.1.5:8000/episodes/ep/files/episode_16x9.mp4"
    assert s["paths"]["video"] == "/data/episodes/ep/episode_16x9.mp4"
    assert s["shorts"][0]["url"].endswith("/shorts/clip_01_9x16.mp4")


def test_inbox_endpoints():
    data = get_settings().data_dir
    (data / "inbox").mkdir(parents=True, exist_ok=True)
    (data / "inbox" / "demo.md").write_text("## Mở đầu\nXin chào các bạn.\n", encoding="utf-8")
    (data / "inbox" / "loi.md").write_text("Một.\n[pasue 2s]\n", encoding="utf-8")
    with TestClient(app) as client:
        r = client.post("/inbox/episodes", json={"path": "/data/inbox/demo.md"})
        assert r.status_code == 201 and r.json()["inbox_file"] == "demo.md"
        ep = r.json()["episode"]["id"]
        bad = client.post("/inbox/episodes", json={"path": "loi.md"})
        assert bad.status_code == 422 and "Dòng 2" in bad.json()["detail"]
        assert client.post("/inbox/episodes", json={"path": "x.md"}).status_code == 404

        summary = client.get(f"/episodes/{ep}/summary").json()
        assert summary["ready"] is False and "episode_16x9.mp4" in summary["missing"]

        moved = client.post("/inbox/archive", json={"path": "demo.md", "to": "done"}).json()
        assert moved["moved_to"] == "inbox/done/demo.md"
        assert client.post("/inbox/archive", json={"path": "loi.md", "to": "trash"}
                           ).status_code == 422
