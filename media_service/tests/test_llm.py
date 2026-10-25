import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.llm.client as client_mod
from app.config import REPO_ROOT, BrandConfig
from app.llm.client import ClaudeCodeClient, LLMError, _json_from_text, generate
from app.llm.schemas import ChaptersLLM, ClipsLLM, MetadataLLM
from app.llm.timing import (
    chapters_from_llm, chapters_text, clips_from_llm, fmt_ts, required_clip_count,
    transcript_lines, youtube_chapters,
)
from app.thumbnail.render import fit_text, render_thumbnail


def segments(n=12, length=4.0, gap=0.5, chapter_every=4):
    segs, t = [], 0.5
    for i in range(n):
        segs.append({"id": f"s{i + 1:04d}", "text": f"Câu số {i + 1}.", "start": round(t, 3),
                     "end": round(t + length, 3), "chapter": i // chapter_every, "speaker": None})
        t += length + gap
    chapters = []
    for c in range(n // chapter_every):
        first, last = segs[c * chapter_every], segs[(c + 1) * chapter_every - 1]
        chapters.append({"index": c, "title": f"Chương {c + 1}", "start": first["start"],
                         "end": last["end"]})
    return {"duration": round(t + 1, 3), "segments": segs, "chapters": chapters}


GOOD_META = {"title": "n8n là gì? Tự động hóa trong 5 phút",
             "description": "Tập này giới thiệu n8n, công cụ tự động hóa mã nguồn mở. " * 3,
             "tags": [f"tag {i}" for i in range(12)],
             "thumbnail_texts": ["n8n là gì?", "Tự động hóa dễ", "Bắt đầu ngay"]}


class FakeClient:
    name, model = "fake", "fake-1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def complete_json(self, system, prompt, schema):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_metadata_schema_rules():
    assert MetadataLLM.model_validate(GOOD_META).tags[0] == "tag 0"
    for bad in ({"title": "x" * 71}, {"tags": ["a"] * 12}, {"tags": ["a"]},
                {"thumbnail_texts": ["a", "b"]}, {"thumbnail_texts": ["a", "b", "c" * 41]}):
        with pytest.raises(ValidationError):
            MetadataLLM.model_validate({**GOOD_META, **bad})
    assert MetadataLLM.model_validate({**GOOD_META, "tags": [f"#t{i}" for i in range(10)]}
                                      ).tags[0] == "t0"


def test_generate_retries_with_feedback():
    fake = FakeClient([{**GOOD_META, "title": "x" * 90}, GOOD_META])
    result, attempts = generate(fake, "sys", "viết metadata", MetadataLLM)
    assert attempts == 2 and result.title == GOOD_META["title"]
    assert "KHÔNG hợp lệ" in fake.prompts[1] and "title" in fake.prompts[1]


def test_generate_gives_up():
    fake = FakeClient([{"title": "x"}] * 3)
    with pytest.raises(LLMError, match="sau 3 lần"):
        generate(fake, "sys", "p", MetadataLLM)


def test_fmt_ts_and_youtube_chapters():
    assert fmt_ts(0) == "00:00" and fmt_ts(75.9) == "01:15" and fmt_ts(3725) == "1:02:05"
    chs = [{"title": "Mở đầu", "start": 0.5}, {"title": "Phần chính", "start": 70.2}]
    assert youtube_chapters(chs)[0] == {"time": "00:00", "title": "Mở đầu", "start": 0.0}
    assert chapters_text(chs) == "00:00 Mở đầu\n01:10 Phần chính"


def test_transcript_lines_contains_ids_timing_and_chapters():
    text = transcript_lines(segments(4, chapter_every=2))
    assert "## Chương 1" in text and "## Chương 2" in text
    assert "[s0001] (00:00, 4.0s) Câu số 1." in text
    assert "##" not in transcript_lines(segments(4), [])


def test_chapters_from_llm():
    seg = segments(12)
    out = chapters_from_llm(ChaptersLLM.model_validate({"chapters": [
        {"title": "Mở", "start_sentence_id": "s0001"},
        {"title": "Giữa", "start_sentence_id": "s0005"}]}), seg)
    assert out[0]["start"] == 0.0 and out[1]["start"] == seg["segments"][4]["start"]
    assert out[-1]["end"] == seg["duration"]
    for chapters, msg in (([("Phần A", "s0002")], "chapter đầu tiên"),
                          ([("Phần A", "s0001"), ("Phần B", "s0099")], "không có câu"),
                          ([("Phần A", "s0001"), ("Phần B", "s0002")], "ngắn hơn 10 giây"),
                          ([("Phần A", "s0001"), ("Phần B", "s0009"), ("Phần C", "s0005")],
                           "tăng dần")):
        res = ChaptersLLM.model_validate({"chapters": [
            {"title": t, "start_sentence_id": i} for t, i in chapters]})
        with pytest.raises(ValueError, match=msg):
            chapters_from_llm(res, seg)


def clip(a, b, title="Clip"):
    return {"start_sentence_id": a, "end_sentence_id": b, "title": title, "hook": "Hook",
            "reason": "Tự đứng được"}


def test_clips_from_llm_maps_to_sentence_boundaries():
    seg = segments(24)  # 4.5 s per sentence
    out = clips_from_llm(ClipsLLM.model_validate({"clips": [
        clip("s0010", "s0016", "Clip B"), clip("s0001", "s0008", "Clip A")]}), seg, 30, 60, 2)
    assert [c["title"] for c in out] == ["Clip A", "Clip B"]  # sorted by time
    s = seg["segments"]
    assert out[0]["start"] == round(s[0]["start"] - 0.2, 3)
    assert out[0]["end"] == round(s[7]["end"] + 0.5, 3)


@pytest.mark.parametrize("clips,msg", [
    ([clip("s0001", "s0003")], "cần 30-60s"),
    ([clip("s0001", "s0020")], "cần 30-60s"),
    ([clip("s0005", "s0001")], "đứng trước"),
    ([clip("s0001", "s0099")], "không tồn tại"),
    ([clip("s0001", "s0008", "Clip A"), clip("s0005", "s0012", "Clip B")], "chồng lên nhau"),
    ([clip("s0001", "s0008")], "ít nhất 2"),
])
def test_clips_from_llm_rejects(clips, msg):
    with pytest.raises(ValueError, match=msg):
        clips_from_llm(ClipsLLM.model_validate({"clips": clips}), segments(24), 30, 60, 2)


def test_required_clip_count():
    assert required_clip_count(62, 3, 30) == 1
    assert required_clip_count(600, 3, 30) == 3
    assert required_clip_count(10, 3, 30) == 1


def test_json_from_text():
    assert _json_from_text('Đây: {"a": 1} xong') == {"a": 1}
    with pytest.raises(LLMError):
        _json_from_text("không có json")


def test_claude_code_client_parses_structured_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"], seen["env"] = cmd, kw["input"], kw["env"]
        out = {"type": "result", "subtype": "success", "is_error": False, "result": "",
               "structured_output": {"ok": True}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(client_mod.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(client_mod.subprocess, "run", fake_run)
    c = ClaudeCodeClient("sonnet", token="tok")
    assert c.complete_json("sys", "prompt", {"type": "object"}) == {"ok": True}
    assert seen["input"] == "prompt" and seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--tools") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}


def test_claude_code_client_errors(monkeypatch):
    monkeypatch.setattr(client_mod.shutil, "which", lambda _: "/usr/bin/claude")
    with pytest.raises(LLMError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        ClaudeCodeClient(token="").complete_json("s", "p", {})
    err = {"is_error": True, "subtype": "success", "terminal_reason": "api_error",
           "result": "Invalid API key"}
    monkeypatch.setattr(client_mod.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 1, json.dumps(err), ""))
    with pytest.raises(LLMError, match="api_error"):
        ClaudeCodeClient(token="x").complete_json("s", "p", {})


FONTS = REPO_ROOT / "assets" / "fonts"


@pytest.mark.skipif(not (FONTS / "BeVietnamPro-Bold.ttf").exists(), reason="fonts missing")
def test_thumbnail_size_and_text_fit():
    img = render_thumbnail(BrandConfig(), "Người ở đâu? Tự động hóa với n8n",
                           FONTS / "BeVietnamPro-Bold.ttf", FONTS / "BeVietnamPro-Regular.ttf",
                           None, REPO_ROOT / "assets" / "logo.png")
    assert img.size == (1280, 720) and img.mode == "RGB"
    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font, lines = fit_text(draw, "Một câu rất dài " * 6, FONTS / "BeVietnamPro-Bold.ttf", 900)
    assert len(lines) <= 3 or font.size == 56
    font, lines = fit_text(draw, "n8n", FONTS / "BeVietnamPro-Bold.ttf", 900)
    assert font.size == 132 and lines == ["n8n"]
