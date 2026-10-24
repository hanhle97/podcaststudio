"""Tolerant parser for script.md.

Format (everything optional except some narration):

    ---
    title: ...
    background: kenburns | loop | gradient
    music: song.mp3
    ---
    ## Chapter
    Narration text...  [pause 1s]  [music up]
    [Name]: dialogue line

All problems are collected (not just the first) and reported with line numbers and a
hint in Vietnamese. Anything that is unambiguous but sloppy becomes a warning.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import yaml

from app.config import ScriptCfg
from app.script.models import (
    Chapter,
    MusicUp,
    Pause,
    Script,
    ScriptIssue,
    ScriptMeta,
    ScriptParseError,
    Sentence,
)

BACKGROUNDS = ("kenburns", "loop", "gradient")
FRONTMATTER_KEYS = ("title", "background", "music")

_HEADING = re.compile(r"^(#{1,6})(?:\s+|(?<=##)(?=\S)|$)(.*?)\s*#*\s*$")
_HR = re.compile(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$")
_FENCE = re.compile(r"^(```|~~~)")
_SPEAKER = re.compile(r"^\[([^\[\]\n]{1,40})\]\s*:\s*(.*)$")
_TAG = re.compile(r"\[([^\[\]\n]*)\]")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_BLOCKQUOTE = re.compile(r"^(>\s*)+")
_LIST = re.compile(r"^([-*+•]|\d{1,3}[.)])\s+")
_EMPHASIS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"~~(.+?)~~"), r"\1"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
]
_PAUSE = re.compile(r"^(pause|nghỉ|nghi|dừng|dung)(?:\s+(.*))?$", re.IGNORECASE)
_MUSIC = re.compile(r"^(music|nhạc|nhac)(?:\s+(.*))?$", re.IGNORECASE)
_MUSIC_UP = re.compile(r"^(up|lên|len)(?:\s+(.*))?$", re.IGNORECASE)
_DURATION = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*(ms|s|sec|secs|second|seconds|giây|giay)?$", re.IGNORECASE
)
_TAG_WORDS = ["pause", "music", "nghỉ", "nhạc", "dừng"]

# Sentence boundary: terminal punctuation (+ closing quotes) followed by whitespace.
_BOUNDARY = re.compile(r"[.!?…]+[\"”’»)\]]*(?=\s)")
_ABBREVIATIONS = {
    "tp", "ts", "ths", "pgs", "gs", "bs", "ks", "ths", "q", "p", "v.v", "vv",
    "mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "no", "tr",
}  # fmt: skip
_SENTENCE_OPENERS = "\"“‘«([-–—'"

PAUSE_HINT = "Dùng [pause 1s], [pause 1.5s] hoặc [pause 500ms]."


def parse_script(text: str, cfg: ScriptCfg | None = None) -> Script:
    """Parse script.md text. Raises ScriptParseError listing every error found."""
    return _Parser(text, cfg or ScriptCfg()).parse()


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in `text`, punctuation kept."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        end = m.end()
        rest = text[end:].lstrip()
        if not rest:
            continue
        nxt = rest[0]
        if not (nxt.isupper() or nxt.isdigit() or nxt in _SENTENCE_OPENERS):
            continue
        if m.group() == ".":
            last = re.search(r"(\S+)$", text[start : m.start()])
            token = last.group(1).lower().strip("(\"'“") if last else ""
            if token in _ABBREVIATIONS or (len(token) == 1 and token.isalpha()):
                continue
        spans.append((start, end))
        start = end + (len(text[end:]) - len(rest))
    if text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return spans


def clean_inline_markdown(line: str) -> str:
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    line = _BLOCKQUOTE.sub("", line)
    line = _LIST.sub("", line)
    for pattern, repl in _EMPHASIS:
        line = pattern.sub(repl, line)
    return line


@dataclass
class _ChapterDraft:
    title: str | None
    line: int | None
    items: list = field(default_factory=list)


class _Parser:
    def __init__(self, text: str, cfg: ScriptCfg):
        self.cfg = cfg
        text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
        self.lines = text.split("\n")
        self.errors: list[ScriptIssue] = []
        self.warnings: list[ScriptIssue] = []
        self.meta = ScriptMeta()
        self.has_headings = False
        self.chapters: list[_ChapterDraft] = [_ChapterDraft(title=None, line=None)]
        self.fragments: list[tuple[str, int]] = []  # pending narration (text, line)
        self.paragraph = 0
        self.paragraph_used = False
        self.speaker: str | None = None
        self.sentence_count = 0

    # --- issue helpers ---------------------------------------------------------

    def error(self, line: int | None, message: str, hint: str | None = None,
              column: int | None = None) -> None:
        self.errors.append(ScriptIssue(
            severity="error", line=line, column=column, message=message, hint=hint,
            source=self.lines[line - 1] if line else None,
        ))

    def warn(self, line: int | None, message: str, hint: str | None = None) -> None:
        self.warnings.append(ScriptIssue(
            severity="warning", line=line, message=message, hint=hint,
            source=self.lines[line - 1] if line else None,
        ))

    # --- main ------------------------------------------------------------------

    def parse(self) -> Script:
        if "\x00" in "".join(self.lines[:50]):
            raise ScriptParseError([ScriptIssue(
                message="File không phải văn bản (có ký tự nhị phân).",
                hint="Lưu kịch bản dưới dạng văn bản UTF-8 (.md hoặc .txt).",
            )])
        body_start = self._frontmatter()
        self._body(body_start)
        chapters = self._finish_chapters()
        if not any(c.sentences for c in chapters) and not self.errors:
            self.error(None, "Kịch bản không có lời đọc nào.",
                       "Thêm ít nhất một câu văn bản ngoài heading và tag điều khiển.")
        if self.errors:
            raise ScriptParseError(self.errors)
        return Script(meta=self.meta, chapters=chapters,
                      has_headings=self.has_headings, warnings=self.warnings)

    # --- frontmatter -------------------------------------------------------------

    def _frontmatter(self) -> int:
        first = next((i for i, ln in enumerate(self.lines) if ln.strip()), None)
        if first is None or self.lines[first].strip() != "---":
            return 0
        close = next((j for j in range(first + 1, len(self.lines))
                      if self.lines[j].strip() in ("---", "...")), None)
        if close is None:
            self.error(first + 1, "Frontmatter mở bằng '---' nhưng không có dòng '---' đóng.",
                       "Thêm một dòng '---' ngay sau các dòng title/background/music, "
                       "hoặc xóa dòng '---' đầu tiên nếu không dùng frontmatter.")
            raise ScriptParseError(self.errors)

        block_lines = self.lines[first + 1 : close]
        offset = first + 2  # 1-based line number of block_lines[0]
        try:
            data = yaml.safe_load("\n".join(block_lines))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            line = offset + mark.line if mark else first + 1
            problem = getattr(e, "problem", None) or "không đọc được"
            self.error(line, f"Frontmatter sai cú pháp YAML ({problem}).",
                       "Mỗi dòng có dạng 'khóa: giá trị'. Nếu giá trị chứa dấu ':' "
                       "hoặc '#', hãy đặt nó trong ngoặc kép, ví dụ title: \"n8n: nhập môn\".")
            return close + 1
        if data is None:
            return close + 1
        if not isinstance(data, dict):
            self.error(offset, "Frontmatter phải là danh sách 'khóa: giá trị'.",
                       "Ví dụ:\n      title: Tên tập\n      background: gradient")
            return close + 1

        def key_line(key: str) -> int:
            for k, ln in enumerate(block_lines):
                if re.match(rf"^\s*{re.escape(str(key))}\s*:", ln):
                    return offset + k
            return offset

        for key, value in data.items():
            name = str(key).strip().lower()
            line = key_line(str(key))
            if name not in FRONTMATTER_KEYS:
                close_match = difflib.get_close_matches(name, FRONTMATTER_KEYS, n=1)
                hint = f"Có phải ý bạn là '{close_match[0]}'?" if close_match else \
                    f"Các khóa hỗ trợ: {', '.join(FRONTMATTER_KEYS)}."
                self.warn(line, f"Khóa frontmatter '{key}' không được hỗ trợ, bỏ qua.", hint)
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, (dict, list)):
                self.error(line, f"Giá trị của '{name}' phải là một chuỗi đơn.")
                continue
            sval = str(value).strip()
            if name == "title":
                self.meta.title = sval
            elif name == "background":
                bg = sval.lower()
                if bg not in BACKGROUNDS:
                    self.error(line, f"background '{sval}' không hợp lệ.",
                               f"Chọn một trong: {', '.join(BACKGROUNDS)}.")
                else:
                    self.meta.background = bg  # type: ignore[assignment]
            elif name == "music":
                self.meta.music = sval
        return close + 1

    # --- body --------------------------------------------------------------------

    def _body(self, start: int) -> None:
        in_comment = False
        in_fence = False
        for idx in range(start, len(self.lines)):
            lineno = idx + 1
            raw = self.lines[idx]

            # HTML comments, possibly spanning lines
            line, in_comment = self._strip_comments(raw, in_comment)
            stripped = line.strip()

            if _FENCE.match(stripped):
                self._flush()
                if not in_fence:
                    self.warn(lineno, "Khối code (```) không được đọc, bỏ qua.")
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            if not stripped:
                self._paragraph_break()
                continue

            heading = _HEADING.match(stripped) if stripped.startswith("#") else None
            if heading:
                self._heading(len(heading.group(1)), heading.group(2), lineno)
                continue

            if _HR.match(stripped):
                self._paragraph_break()
                continue

            content = clean_inline_markdown(stripped)
            speaker = _SPEAKER.match(content)
            if speaker and not self._is_control_tag(speaker.group(1)):
                self._set_speaker(speaker.group(1).strip(), lineno)
                content = speaker.group(2)
                if not content.strip():
                    self.error(lineno, f"Dòng thoại của [{speaker.group(1).strip()}] bị trống.",
                               "Viết câu thoại ngay sau dấu ':' trên cùng dòng.")
                    continue
            self._inline(content, raw, lineno)

        if in_comment:
            self.warn(None, "Có chú thích '<!--' không được đóng bằng '-->'; "
                            "phần sau nó bị bỏ qua.")
        self._flush()

    @staticmethod
    def _strip_comments(raw: str, in_comment: bool) -> tuple[str, bool]:
        out = []
        i = 0
        while i < len(raw):
            if in_comment:
                end = raw.find("-->", i)
                if end < 0:
                    return "".join(out), True
                i, in_comment = end + 3, False
            else:
                begin = raw.find("<!--", i)
                if begin < 0:
                    out.append(raw[i:])
                    break
                out.append(raw[i:begin])
                i, in_comment = begin + 4, True
        return "".join(out), in_comment

    def _is_control_tag(self, inner: str) -> bool:
        inner = inner.strip()
        return bool(_PAUSE.match(inner) or _MUSIC.match(inner))

    def _heading(self, level: int, title: str, lineno: int) -> None:
        self._flush()
        title = clean_inline_markdown(title).strip()
        if level == 1:
            if not self.meta.title and title:
                self.meta.title = title
            else:
                self.warn(lineno, "Heading '#' được bỏ qua (tiêu đề đã có).",
                          "Dùng '##' để tạo chapter.")
            return
        if level > 2:
            self.warn(lineno, f"Heading cấp {level} ('{'#' * level}') không tạo chapter "
                              "và không được đọc.", "Dùng '##' để tạo chapter mới.")
            return
        if not title:
            self.error(lineno, "Heading '##' không có tên chapter.",
                       "Viết tên chapter sau '## ', ví dụ: ## Mở đầu")
            return
        self.has_headings = True
        current = self.chapters[-1]
        if current.title is None and current.line is None:
            if current.items:
                current.title = "Mở đầu"
                self.warn(lineno, "Có lời đọc trước heading '##' đầu tiên; "
                                  "phần đó được gom vào chapter 'Mở đầu'.")
            else:
                self.chapters.pop()
        self.chapters.append(_ChapterDraft(title=title, line=lineno))
        self._paragraph_break()

    def _set_speaker(self, name: str, lineno: int) -> None:
        if name != self.speaker:
            self._flush()
            self._paragraph_break(force=True)
        self.speaker = name

    def _paragraph_break(self, force: bool = False) -> None:
        self._flush()
        if self.paragraph_used:
            self.paragraph += 1
            self.paragraph_used = False
        if not force:
            self.speaker = None

    def _inline(self, content: str, raw: str, lineno: int) -> None:
        pos = 0
        for m in _TAG.finditer(content):
            self._add_text(content[pos : m.start()], raw, lineno)
            pos = m.end()
            self._tag(m.group(1), raw, lineno, at_line_start=not content[: m.start()].strip(),
                      rest=content[m.end():])
        self._add_text(content[pos:], raw, lineno)

    def _tag(self, inner: str, raw: str, lineno: int, at_line_start: bool, rest: str) -> None:
        column = raw.find(f"[{inner}]") + 1 or None
        body = " ".join(inner.split())

        pause = _PAUSE.match(body)
        if pause:
            seconds = self._duration(pause.group(2), lineno, column, f"[{inner}]")
            if seconds:
                self._flush()
                self._current_items().append(Pause(seconds=seconds, line=lineno))
            return

        music = _MUSIC.match(body)
        if music:
            arg = (music.group(2) or "").strip()
            if not arg:
                self.warn(lineno, f"[{inner}] được hiểu là [music up].")
                up = None
            else:
                up = _MUSIC_UP.match(arg)
                if not up:
                    self.error(lineno, f"Tag [{inner}] không được hỗ trợ.",
                               "Chỉ có [music up] (nhạc lên ~3 giây) hoặc [music up 5s].",
                               column)
                    return
            seconds = self.cfg.music_up_seconds
            if up and up.group(2):
                seconds = self._duration(up.group(2), lineno, column, f"[{inner}]") or 0
                if not seconds:
                    return
            self._flush()
            self._current_items().append(MusicUp(seconds=seconds, line=lineno))
            return

        word = body.split(" ")[0].lower() if body else ""
        guess = difflib.get_close_matches(word, _TAG_WORDS, n=1, cutoff=0.6)
        if guess:
            args = body.split(" ", 1)[1] if " " in body else ""
            if guess[0] in ("pause", "nghỉ", "dừng"):
                example = f"[pause {args or '1s'}]"
            else:
                example = "[music up]"
            self.error(lineno, f"Tag [{inner}] không hợp lệ.",
                       f"Có phải ý bạn là {example}?", column)
            return
        if at_line_start and rest.strip() and 0 < len(body.split()) <= 4:
            self.warn(lineno, f"[{inner}] được hiểu là người nói (thiếu dấu ':').",
                      f"Viết [{inner}]: câu thoại")
            self._set_speaker(body, lineno)
            return
        self.warn(lineno, f"Tag [{inner}] không được hỗ trợ nên bị bỏ qua (không đọc).",
                  "Tag hỗ trợ: [pause Ns], [music up], và [Tên]: ở đầu dòng.")

    def _duration(self, arg: str | None, lineno: int, column: int | None,
                  tag: str) -> float | None:
        if not arg or not arg.strip():
            self.warn(lineno, f"{tag} không ghi thời lượng, dùng 1 giây.", PAUSE_HINT)
            return 1.0
        m = _DURATION.match(arg.strip())
        if not m:
            self.error(lineno, f"Thời lượng trong {tag} sai cú pháp.", PAUSE_HINT, column)
            return None
        value = float(m.group(1).replace(",", "."))
        unit = (m.group(2) or "").lower()
        if unit == "ms":
            value /= 1000
        elif not unit:
            self.warn(lineno, f"{tag} không ghi đơn vị, hiểu là {value:g} giây.", PAUSE_HINT)
        if value <= 0:
            self.warn(lineno, f"{tag} có thời lượng 0, bỏ qua.")
            return None
        if value > self.cfg.max_pause_seconds:
            self.warn(lineno, f"{tag} dài hơn {self.cfg.max_pause_seconds:g} giây, "
                              f"giảm xuống {self.cfg.max_pause_seconds:g} giây.")
            value = self.cfg.max_pause_seconds
        return round(value, 3)

    def _add_text(self, text: str, raw: str, lineno: int) -> None:
        if not text.strip():
            return
        bracket = text.find("[")
        if bracket >= 0 and "]" not in text[bracket:]:
            tail = text[bracket + 1 :].strip().split(" ")[0].lower()
            if difflib.get_close_matches(tail, _TAG_WORDS, n=1, cutoff=0.6):
                self.error(lineno, "Tag thiếu dấu đóng ']'.", PAUSE_HINT,
                           raw.find("[") + 1 or None)
                return
            self.warn(lineno, "Dấu '[' không có ']' đi kèm; được đọc như văn bản.")
        self.fragments.append((text.strip(), lineno))

    def _current_items(self) -> list:
        return self.chapters[-1].items

    def _flush(self) -> None:
        if not self.fragments:
            return
        joined = ""
        starts: list[tuple[int, int]] = []  # (offset, line)
        for text, line in self.fragments:
            if joined:
                joined += " "
            starts.append((len(joined), line))
            joined += text
        self.fragments = []

        for begin, end in split_sentences(joined):
            sentence = " ".join(joined[begin:end].split())
            if not any(ch.isalnum() for ch in sentence):
                continue
            line = next(ln for off, ln in reversed(starts) if off <= begin)
            self.sentence_count += 1
            if len(sentence) > self.cfg.long_sentence_warn_chars:
                self.warn(line, f"Câu dài {len(sentence)} ký tự; nên tách thành câu ngắn "
                                "hơn để phụ đề và ngữ điệu tự nhiên.")
            self._current_items().append(Sentence(
                id=f"s{self.sentence_count:04d}", text=sentence, speaker=self.speaker,
                paragraph=self.paragraph, line=line,
            ))
            self.paragraph_used = True

    def _finish_chapters(self) -> list[Chapter]:
        chapters: list[Chapter] = []
        for draft in self.chapters:
            if not any(isinstance(i, Sentence) for i in draft.items):
                if draft.title is not None:
                    self.warn(draft.line, f"Chapter '{draft.title}' không có lời đọc, bỏ qua.")
                continue
            chapters.append(Chapter(index=len(chapters), title=draft.title,
                                    line=draft.line, items=draft.items))
        return chapters
