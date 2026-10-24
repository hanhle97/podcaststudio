import pytest

from app.script import MusicUp, Pause, ScriptParseError, Sentence, parse_script, split_sentences

FULL = """---
title: n8n cho người mới
background: kenburns
music: chill.mp3
---

## Mở đầu
Xin chào các bạn, hôm nay mình sẽ nói về n8n.
[pause 1s]

## n8n là gì
n8n là một công cụ tự động hóa quy trình. Nó mã nguồn mở!
[music up]

## Kết luận
Cảm ơn các bạn đã nghe.
"""


def errors_of(text: str):
    with pytest.raises(ScriptParseError) as exc:
        parse_script(text)
    return exc.value.issues


def test_full_script_with_frontmatter():
    s = parse_script(FULL)
    assert s.meta.title == "n8n cho người mới"
    assert s.meta.background == "kenburns"
    assert s.meta.music == "chill.mp3"
    assert s.has_headings
    assert [c.title for c in s.chapters] == ["Mở đầu", "n8n là gì", "Kết luận"]
    assert [c.line for c in s.chapters] == [7, 11, 15]
    ch0 = s.chapters[0].items
    assert isinstance(ch0[0], Sentence) and ch0[0].line == 8
    assert isinstance(ch0[1], Pause) and ch0[1].seconds == 1.0
    ch1 = s.chapters[1].items
    assert [i.text for i in ch1 if isinstance(i, Sentence)] == [
        "n8n là một công cụ tự động hóa quy trình.", "Nó mã nguồn mở!"]
    assert isinstance(ch1[-1], MusicUp) and ch1[-1].seconds == 3
    assert [x.id for x in s.sentences] == ["s0001", "s0002", "s0003", "s0004"]
    assert s.warnings == []


def test_no_frontmatter_with_headings():
    s = parse_script("## Một\nCâu một.\n\n## Hai\nCâu hai.\n")
    assert s.meta.title is None and s.meta.background is None
    assert [c.title for c in s.chapters] == ["Một", "Hai"]


def test_plain_text_only():
    s = parse_script("Người ở đâu? Mình ở Hà Nội.\nĐây là dòng thứ hai.")
    assert not s.has_headings
    assert len(s.chapters) == 1 and s.chapters[0].title is None
    assert [x.text for x in s.sentences] == [
        "Người ở đâu?", "Mình ở Hà Nội.", "Đây là dòng thứ hai."]
    assert [x.line for x in s.sentences] == [1, 1, 2]


def test_crlf_and_bom():
    s = parse_script("﻿---\r\ntitle: A\r\n---\r\nXin chào.\r\n")
    assert s.meta.title == "A" and s.sentences[0].text == "Xin chào."


def test_h1_becomes_title():
    s = parse_script("# Tập 1: n8n\n\nNội dung.")
    assert s.meta.title == "Tập 1: n8n"


def test_multiple_speakers():
    s = parse_script(
        "Mình mở đầu.\n\n[Lan]: Chào anh. Hôm nay vui quá.\n[Minh]: Chào Lan.\n"
        "Tiếp tục của Minh.\n\nQuay lại giọng chính.")
    got = [(x.speaker, x.text) for x in s.sentences]
    assert got == [
        (None, "Mình mở đầu."),
        ("Lan", "Chào anh."), ("Lan", "Hôm nay vui quá."),
        ("Minh", "Chào Lan."), ("Minh", "Tiếp tục của Minh."),
        (None, "Quay lại giọng chính."),
    ]
    assert s.speakers == [None, "Lan", "Minh"]
    # speaker change starts a new paragraph; same speaker stays in it
    paras = [x.paragraph for x in s.sentences]
    assert paras[1] == paras[2] != paras[3] == paras[4]


def test_speaker_without_colon_is_warning():
    s = parse_script("[Lan] Chào cả nhà.")
    assert s.sentences[0].speaker == "Lan"
    assert any("thiếu dấu ':'" in w.message for w in s.warnings)


def test_pause_units_and_inline_tags():
    s = parse_script("Một. [pause 500ms] Hai. [pause 1,5s] Ba. [nghỉ 2 giây] Bốn. [music up 5s]")
    items = s.chapters[0].items
    pauses = [i.seconds for i in items if isinstance(i, Pause)]
    assert pauses == [0.5, 1.5, 2.0]
    assert isinstance(items[-1], MusicUp) and items[-1].seconds == 5
    # inline tags don't break the paragraph
    assert len({x.paragraph for x in s.sentences}) == 1


def test_pause_without_unit_or_value_warns():
    s = parse_script("A. [pause 2] B. [pause] C.")
    assert [i.seconds for i in s.chapters[0].items if isinstance(i, Pause)] == [2.0, 1.0]
    assert len(s.warnings) == 2


def test_pause_clamped():
    s = parse_script("A. [pause 99s]")
    assert s.chapters[0].items[-1].seconds == 30


def test_bad_tags_report_line_and_hint():
    issues = errors_of("Câu một.\nCâu hai [pasue 1s].\n[pause abc]\n[music down]\n[pause 1s\n")
    by_line = {i.line: i for i in issues}
    assert set(by_line) == {2, 3, 4, 5}
    assert "[pause 1s]" in by_line[2].hint
    assert by_line[2].column == 9
    assert "sai cú pháp" in by_line[3].message
    assert "[music up]" in by_line[4].hint
    assert "']'" in by_line[5].message
    text = str(ScriptParseError(issues))
    assert "Dòng 2, cột 9" in text and "Gợi ý" in text


def test_unknown_tag_is_ignored_with_warning():
    s = parse_script("Xin chào [cười] các bạn.")
    assert s.sentences[0].text == "Xin chào các bạn."
    assert any("[cười]" in w.message for w in s.warnings)


def test_frontmatter_not_closed():
    issues = errors_of("---\ntitle: abc\n\nXin chào.")
    assert issues[0].line == 1 and "đóng" in issues[0].message


def test_frontmatter_yaml_error_line():
    issues = errors_of("---\nbackground: gradient\ntitle: n8n: nhập môn\n---\nXin chào.")
    assert issues[0].line == 3
    assert "ngoặc kép" in issues[0].hint


def test_invalid_background_and_unknown_key():
    with pytest.raises(ScriptParseError) as exc:
        parse_script("---\nbackgound: loop\nbackground: kenburns | loop | gradient\n---\nA.")
    assert exc.value.issues[0].line == 3
    s = parse_script("---\nbackgound: loop\n---\nA.")
    assert s.warnings[0].line == 2 and "background" in s.warnings[0].hint


def test_empty_frontmatter_values_are_optional():
    s = parse_script("---\ntitle:\nmusic: ''\n---\nA.")
    assert s.meta.title is None and s.meta.music is None


def test_empty_script():
    assert "không có lời đọc" in errors_of("---\ntitle: x\n---\n\n## A\n[pause 1s]\n")[0].message
    assert errors_of("")


def test_collects_multiple_errors():
    issues = errors_of("##\n[pause x]\nA.")
    assert [i.line for i in issues] == [1, 2]


def test_text_before_first_heading_goes_to_intro():
    s = parse_script("Lời chào.\n\n## Phần 1\nNội dung.")
    assert [c.title for c in s.chapters] == ["Mở đầu", "Phần 1"]


def test_empty_chapter_dropped_with_warning():
    s = parse_script("## A\nNội dung.\n## B\n[pause 1s]\n## C\nKết.")
    assert [c.title for c in s.chapters] == ["A", "C"]
    assert [c.index for c in s.chapters] == [0, 1]
    assert any("'B'" in w.message for w in s.warnings)


def test_markdown_noise_removed():
    s = parse_script(
        "<!-- ghi chú\nnhiều dòng -->\n- **Đậm** và *nghiêng* với [link](http://x.y).\n"
        "> Trích dẫn `code`.\n---\n```\nbỏ qua\n```\n### Phụ\nHết.")
    assert [x.text for x in s.sentences] == [
        "Đậm và nghiêng với link.", "Trích dẫn code.", "Hết."]


def test_sentence_split_rules():
    text = "Giá là 3.5 triệu. TP. HCM rất đông, v.v. và nhiều thứ khác... Thật không? Có!"
    parts = [text[a:b] for a, b in split_sentences(text)]
    assert parts == ["Giá là 3.5 triệu.", "TP. HCM rất đông, v.v. và nhiều thứ khác...",
                     "Thật không?", "Có!"]


def test_long_sentence_warning():
    s = parse_script("Từ " * 120 + ".")
    assert any("Câu dài" in w.message for w in s.warnings)
