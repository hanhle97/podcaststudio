from app.config import BrandConfig
from app.subtitles.build import (
    Word, ass_color, ass_time, build_cues, karaoke_groups, split_sentence, srt_time, to_ass,
    to_srt, wrap,
)


def words(text: str, sid: str = "s1", chapter: int = 0, start: float = 0.0, step: float = 0.3):
    out = []
    for i, w in enumerate(text.split()):
        out.append(Word(w, round(start + i * step, 3), round(start + i * step + 0.25, 3), sid,
                        chapter))
    return out


LONG = ("n8n là một công cụ tự động hóa quy trình theo kiểu kéo thả, bạn nối các bước với "
        "nhau thành một luồng, ví dụ nhận email rồi gửi thông báo qua Telegram.")


def test_wrap_balances_two_lines():
    assert wrap("Xin chào các bạn".split(), 42, 2) == ["Xin chào các bạn"]
    lines = wrap("n8n là một công cụ tự động hóa quy trình theo kiểu kéo thả".split(), 42, 2)
    assert len(lines) == 2 and all(len(x) <= 42 for x in lines)
    assert abs(len(lines[0]) - len(lines[1])) < 12
    assert wrap(("rất " * 30).split(), 42, 2) is None
    assert wrap("Người mới thường hỏi: có cần biết lập trình không?".split(), 42, 2) == [
        "Người mới thường hỏi:", "có cần biết lập trình không?"]


def test_cues_respect_limits_and_keep_text():
    ws = words(LONG)
    cues = build_cues(ws)
    for c in cues:
        assert 1 <= len(c.lines) <= 2 and all(len(line) <= 42 for line in c.lines)
    assert " ".join(" ".join(c.lines) for c in cues) == LONG
    # prefers to cut at clause boundaries
    assert any(c.lines[-1].endswith(",") for c in cues[:-1])


def test_cues_never_span_sentences_or_chapters():
    ws = words("Câu một ngắn.", "s1", 0) + words("Câu hai ở chapter khác.", "s2", 1, start=2.0)
    cues = build_cues(ws)
    assert [c.lines for c in cues] == [["Câu một ngắn."], ["Câu hai ở chapter khác."]]
    assert cues[0].end <= cues[1].start - 0.05 + 1e-9


def test_cue_timing_hold_and_no_overlap():
    ws = words("Một hai.", "s1") + words("Ba bốn.", "s2", start=0.6)
    cues = build_cues(ws, hold=0.4, min_duration=0.8)
    assert cues[0].start == 0.0 and cues[0].end == 0.55  # capped by next cue
    assert cues[1].end == 1.55  # last word end 1.15 + hold


def test_long_sentence_is_balanced():
    text = ("Hôm nay mình sẽ nói về n8n, một công cụ giúp bạn tự động hóa những công việc "
            "lặp đi lặp lại hằng ngày.")
    chunks = split_sentence(words(text), 42, 2)
    joined = [" ".join(w.word for w in c) for c in chunks]
    assert joined == ["Hôm nay mình sẽ nói về n8n,",
                      "một công cụ giúp bạn tự động hóa những công việc lặp đi lặp lại hằng ngày."]


def test_no_lonely_last_word():
    ws = words("một hai ba bốn năm sáu bảy tám chín mười mười một mười hai mười ba mười bốn "
               "mười lăm mười sáu mười bảy mười tám hết")
    chunks = split_sentence(ws, 42, 2)
    assert len(chunks[-1]) >= 2


def test_srt_format():
    srt = to_srt(build_cues(words("Người ở đâu?")))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,") and "Người ở đâu?" in srt
    assert srt_time(3725.5) == "01:02:05,500"


def test_karaoke_groups_sizes():
    groups = karaoke_groups(words("một hai ba bốn năm"))
    assert [len(g) for g in groups] == [3, 2]  # 4+1 would orphan a word
    groups = karaoke_groups(words("Xin chào, các bạn thân mến"))
    assert [" ".join(w.word for w in g) for g in groups] == ["Xin chào,", "các bạn thân mến"]
    assert all(2 <= len(g) <= 4 for g in karaoke_groups(words(LONG)))
    assert [len(g) for g in karaoke_groups(words("Chào."))] == [1]


def test_karaoke_groups_stay_in_sentence():
    ws = words("Một hai ba.", "s1") + words("Bốn năm.", "s2", start=1)
    assert [[w.sentence_id for w in g] for g in karaoke_groups(ws)] == [
        ["s1", "s1", "s1"], ["s2", "s2"]]


def test_ass_document():
    groups = karaoke_groups(words("Người ở đâu?", step=0.5))
    doc = to_ass(groups, BrandConfig())
    assert "PlayResX: 1080" in doc and "PlayResY: 1920" in doc
    assert "Style: Karaoke,Be Vietnam Pro,96,&H0000B7F5,&H00FCFAF8" in doc
    assert "Dialogue: 0,0:00:00.00,0:00:01.25,Karaoke,,0,0,0,,{\\k50}Người {\\k50}ở {\\k25}đâu?" \
        in doc
    assert ass_color("#F5B700") == "&H0000B7F5" and ass_time(61.234) == "0:01:01.23"
