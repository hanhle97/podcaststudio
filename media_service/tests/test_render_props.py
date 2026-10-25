from app.config import BrandConfig
from app.render.props import background_spec, build_props, chapter_titles, heuristic_clips


def seg(i, start, end, chapter, text="Câu."):
    return {"id": f"s{i:04d}", "text": text, "start": start, "end": end, "chapter": chapter}


def test_background_falls_back_to_gradient():
    assert background_spec("kenburns", [], None, 0)["kind"] == "gradient"
    assert background_spec("loop", ["bg/a.jpg"], None, 0)["kind"] == "gradient"
    kb = background_spec("kenburns", ["bg/a.jpg"], "bg/v.mp4", 10)
    assert kb == {"kind": "kenburns", "images": ["bg/a.jpg"], "video": None,
                  "videoDurationSec": 0}
    lp = background_spec("loop", ["bg/a.jpg"], "bg/v.mp4", 10.0)
    assert lp["video"] == "bg/v.mp4" and lp["images"] == [] and lp["videoDurationSec"] == 10.0


def test_chapter_titles_fill_missing():
    out = chapter_titles([{"index": 0, "title": None, "start": 0, "end": 5},
                          {"index": 1, "title": "Hai", "start": 5, "end": 9}])
    assert [c["title"] for c in out] == ["Phần 1", "Hai"]


def test_build_props_contract():
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "sentence_id": "s0001",
              "chapter": 0} for i, w in enumerate("Người ở đâu, bạn có biết không?".split())]
    segments = {"duration": 5.0, "chapters": [{"index": 0, "title": "Mở đầu", "start": 0,
                                               "end": 5.0}], "segments": []}
    karaoke = [{"start": 0, "end": 1, "text": "Người ở", "sentence_id": "s0001", "chapter": 0,
                "words": [{"word": "Người", "start": 0, "end": 0.3}]}]
    p = build_props(segments=segments, words=words, karaoke=karaoke, brand=BrandConfig(),
                    title="Tập 1", background=background_spec("gradient", [], None, 0),
                    logo="logo.png")
    assert set(p) == {"fps", "durationSec", "audio", "viz", "title", "brand", "background",
                      "chapters", "cues", "karaoke"}
    assert p["fps"] == 30 and p["durationSec"] == 5.0 and p["brand"]["logo"] == "logo.png"
    assert p["brand"]["colors"]["primary"] == "#F5B700"
    # every word lands in exactly one cue line, in order
    flat = [w["word"] for c in p["cues"] for line in c["lines"] for w in line]
    assert flat == [w["word"] for w in words]
    assert "sentence_id" not in p["karaoke"][0]


def test_heuristic_clips_sentence_aligned_and_non_overlapping():
    segs = [seg(i, i * 5.0, i * 5.0 + 4.5, i // 6) for i in range(24)]  # 24 x 5s, 4 chapters
    clips = heuristic_clips({"segments": segs}, 30, 60, 5)
    assert 1 <= len(clips) <= 5
    starts = {s["start"] for s in segs}
    for c in clips:
        assert 30 <= c["end"] - c["start"] <= 61
        assert c["start"] == 0 or round(c["start"] + 0.2, 3) in starts  # 0.2s lead-in
    spans = sorted((c["start"], c["end"]) for c in clips)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_heuristic_clips_too_short_episode():
    segs = [seg(i, i * 3.0, i * 3.0 + 2.5, 0) for i in range(5)]  # 15 s total
    assert heuristic_clips({"segments": segs}, 30, 60, 5) == []
