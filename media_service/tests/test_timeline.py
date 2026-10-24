from app.audio.assemble import compute_timeline
from app.config import AudioCfg
from app.script import parse_script

SCRIPT = """## Một
Câu một. Câu hai.

Câu ba. [pause 2s] Câu bốn.
[music up]

## Hai
Câu năm.
"""


def test_gaps_pauses_and_chapters():
    s = parse_script(SCRIPT)
    tl = compute_timeline(s, {x.id: 1.0 for x in s.sentences}, AudioCfg())
    seg = {x.text: (x.start, x.end) for x in tl.segments}
    assert seg["Câu một."] == (0.5, 1.5)    # lead-in 500ms
    assert seg["Câu hai."] == (1.75, 2.75)  # +250ms sentence gap
    assert seg["Câu ba."] == (3.35, 4.35)  # +600ms paragraph gap
    assert seg["Câu bốn."] == (6.6, 7.6)    # +2s pause, +250ms
    assert seg["Câu năm."] == (11.8, 12.8)  # +3s music up, +1.2s chapter gap
    assert tl.duration == 13.8            # +1s tail
    assert [(b.kind, b.start, b.end) for b in tl.breaks] == [
        ("pause", 4.35, 6.35), ("music_up", 7.6, 10.6), ("chapter", 10.6, 11.8)]
    assert [(c.title, c.start, c.end) for c in tl.chapters] == [
        ("Một", 0.5, 11.8), ("Hai", 11.8, 13.8)]
    assert tl.segments[0].chapter_title == "Một" and tl.segments[-1].chapter == 1


def test_speech_text_kept_separately():
    s = parse_script("Dùng n8n.")
    tl = compute_timeline(s, {"s0001": 1.0}, AudioCfg(), {"s0001": "Dùng en ây en."})
    assert tl.segments[0].text == "Dùng n8n."
    assert tl.segments[0].speech_text == "Dùng en ây en."
