from app.align.align import align_sentence, align_tokens, normalize_token, spoken_tokens
from app.align.whisper import WhisperWord as W, pick_device

PRON = {"n8n": "en ây en"}


def test_normalize_token():
    assert normalize_token("Người,") == "người"
    assert normalize_token("“n8n”") == "n8n"
    assert normalize_token("—") == ""
    # decomposed and precomposed Vietnamese compare equal
    assert normalize_token("Việt") == normalize_token("Việt")


def test_spoken_tokens_follow_pronunciation():
    assert spoken_tokens(["Dùng", "n8n,", "nhé."], PRON) == [
        ("dùng", 0), ("en", 1), ("ây", 1), ("en", 1), ("nhé", 2)]


def test_exact_match_uses_whisper_times():
    heard = [W(" Xin", 0.0, 0.3), W(" chào", 0.3, 0.6), W(" bạn.", 0.7, 1.0)]
    out = align_sentence("Xin chào bạn.", heard, 1.2, {})
    assert [(w.word, w.start, w.end, w.matched) for w in out] == [
        ("Xin", 0.0, 0.3, True), ("chào", 0.3, 0.6, True), ("bạn.", 0.7, 1.0, True)]


def test_pronounced_word_spans_its_tokens():
    heard = [W("Dùng", 0.0, 0.3), W("en", 0.35, 0.5), W("ây", 0.5, 0.6), W("en", 0.6, 0.8),
             W("nhé", 0.9, 1.1)]
    out = align_sentence("Dùng n8n, nhé.", heard, 1.2, PRON)
    assert out[1].word == "n8n," and (out[1].start, out[1].end) == (0.35, 0.8)
    assert out[1].matched


def test_missing_words_are_interpolated_by_length():
    heard = [W("một", 0.0, 0.4), W("bốn", 1.6, 2.0)]
    out = align_sentence("một hai ba bốn", heard, 2.0, {})
    assert (out[0].start, out[3].start) == (0.0, 1.6)
    hai, ba = out[1], out[2]
    assert not hai.matched and not ba.matched
    assert 0.4 <= hai.start < hai.end <= ba.start < ba.end <= 1.6


def test_misheard_span_is_distributed():
    # whisper writes "Java Script" for "JavaScript": replaced block keeps its time range
    heard = [W("biết", 0.0, 0.3), W("Java", 0.3, 0.6), W("Script", 0.6, 1.0), W("thôi", 1.1, 1.4)]
    out = align_sentence("biết JavaScript thôi", heard, 1.5, {})
    assert (out[1].start, out[1].end) == (0.3, 1.0) and not out[1].matched
    assert out[2].matched


def test_nothing_heard_spreads_over_clip():
    out = align_sentence("ab abcd", [], 3.0, {})
    assert out[0].start == 0.0 and out[1].end == 3.0
    assert abs(out[0].end - 1.0) < 1e-9  # 2 of 6 characters


def test_times_monotonic_and_clamped():
    heard = [W("a", 0.5, 0.6), W("b", 0.2, 0.3), W("c", 2.5, 3.5)]
    times = align_tokens(["a", "b", "c"], heard, 3.0)
    starts = [s for s, _, _ in times]
    assert starts == sorted(starts) and all(0 <= s <= e <= 3.0 for s, e, _ in times)


def test_punctuation_only_word_sticks_to_neighbour():
    out = align_sentence("Xin chào — bạn", [W("Xin", 0, .2), W("chào", .2, .5), W("bạn", .7, 1)],
                         1.0, {})
    assert out[2].word == "—" and out[2].start == out[2].end == 0.5


def test_pick_device_cpu_forced():
    assert pick_device("cpu") == ("cpu", "int8")
