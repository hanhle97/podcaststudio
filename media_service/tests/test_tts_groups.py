import numpy as np

from app.align.whisper import WhisperWord
from app.script import parse_script
from app.tts.synthesize import coverage, plan_groups, quietest, score_and_cuts
from app.tts.text import speakable

SCRIPT = """## Một
Câu một ngắn. Câu hai cũng ngắn. Câu ba thì dài hơn hẳn hai câu trước nhiều lắm.

Đoạn mới. [pause 1s] Sau tag.
[Lan]: Lan nói.

## Hai
Chương hai.
"""


def groups_of(max_chars, voices=None):
    groups, warnings = plan_groups(parse_script(SCRIPT), voices or {}, "me", {}, max_chars)
    return [[s.text for s in g.sentences] for g in groups], warnings


def test_groups_stay_within_paragraph_tags_voice_and_length():
    groups, warnings = groups_of(40, {"Lan": "lan"})
    assert groups == [
        ["Câu một ngắn.", "Câu hai cũng ngắn."],
        ["Câu ba thì dài hơn hẳn hai câu trước nhiều lắm."],
        ["Đoạn mới."], ["Sau tag."], ["Lan nói."], ["Chương hai."],
    ]
    assert warnings == []


def test_zero_max_chars_reads_one_sentence_per_request():
    groups, _ = groups_of(0)
    assert all(len(g) == 1 for g in groups) and len(groups) == 7


def test_unknown_speaker_warns_and_uses_default_voice():
    groups, warnings = plan_groups(parse_script(SCRIPT), {}, "me", {}, 170)
    assert all(g.voice_id == "me" for g in groups)
    assert len(warnings) == 1 and "Lan" in warnings[0]


def test_speakable_drops_quotes_and_applies_pronunciation():
    assert speakable("Nó nói: “Dùng n8n.”", {"n8n": "en ây en"}) == "Nó nói: Dùng en ây en."


def words(*spec):
    return [WhisperWord(w, s, e) for w, s, e in spec]


def two_sentence_group():
    g, _ = plan_groups(parse_script("Một hai. Ba bốn."), {}, "me", {}, 170)
    assert len(g) == 1 and len(g[0].sentences) == 2
    return g[0]


def test_cut_lands_in_the_silence_between_sentences():
    rate = 1000
    samples = np.full(2000, 3000, dtype=np.int16)
    samples[900:1000] = 0  # pause between the sentences
    heard = words(("một", 0.0, 0.4), ("hai", 0.4, 0.85), ("ba", 1.05, 1.5), ("bốn", 1.5, 1.9))
    score, cuts = score_and_cuts(two_sentence_group(), heard, samples, rate, {})
    assert score == 1.0
    assert len(cuts) == 1 and 0.9 <= cuts[0] <= 1.0


def test_skipped_words_lower_the_score():
    samples = np.zeros(2000, dtype=np.int16)
    heard = words(("một", 0.0, 0.4), ("bốn", 1.5, 1.9))
    score, _ = score_and_cuts(two_sentence_group(), heard, samples, 1000, {})
    assert score < 0.6


def test_quietest_finds_the_lowest_energy_frame():
    samples = np.full(1000, 1000, dtype=np.int16)
    samples[600:610] = 0
    assert abs(quietest(samples, 1000, 0.2, 0.9) - 0.605) < 0.006


def test_whisper_spelling_of_dictionary_words_counts_as_heard():
    g, _ = plan_groups(parse_script("Dùng n8n và API."), {}, "me",
                       {"n8n": "en ây en", "API": "ây pi ai"}, 170)
    heard = words(("Dùng", 0.0, 0.3), ("N8N", 0.3, 0.9), ("và", 0.9, 1.1), ("API.", 1.1, 1.6))
    score, _ = score_and_cuts(g[0], heard, np.zeros(2000, dtype=np.int16), 1000,
                              {"n8n": "en ây en", "API": "ây pi ai"})
    assert score == 1.0


def test_mishearing_is_not_a_skip_but_a_missing_phrase_is():
    script = "nói về en ây en một công cụ được giao nhiệm vụ".split()
    misheard = words(*[(w, 0, 0) for w in "nói về NAN một công cụ được giao nhiệm vụ".split()])
    skipped = words(*[(w, 0, 0) for w in "nói về en ây en một công cụ".split()])
    assert coverage(script, misheard) > 0.9
    assert coverage(script, skipped) < 0.7
