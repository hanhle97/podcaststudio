"""Data model of a parsed script (script.json)."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.config import BackgroundKind


class ScriptIssue(BaseModel):
    """A parse error or warning, pointing at a line of script.md."""

    severity: Literal["error", "warning"] = "error"
    line: int | None = None
    column: int | None = None
    message: str
    hint: str | None = None
    source: str | None = None  # the offending line, for context

    def __str__(self) -> str:
        where = f"Dòng {self.line}" if self.line else "Kịch bản"
        if self.line and self.column:
            where += f", cột {self.column}"
        text = f"{where}: {self.message}"
        if self.source:
            text += f'\n    > {self.source.strip()}'
        if self.hint:
            text += f"\n    Gợi ý: {self.hint}"
        return text


class ScriptParseError(Exception):
    def __init__(self, issues: list[ScriptIssue]):
        self.issues = issues
        super().__init__(self.summary())

    def summary(self) -> str:
        n = len(self.issues)
        head = f"Kịch bản có {n} lỗi:" if n > 1 else "Kịch bản có lỗi:"
        return head + "\n" + "\n".join(str(i) for i in self.issues)


class Sentence(BaseModel):
    kind: Literal["sentence"] = "sentence"
    id: str
    text: str  # exactly what subtitles show (markdown removed, tags removed)
    speaker: str | None = None  # None = the channel owner's (cloned) voice
    paragraph: int  # sentences sharing a paragraph number get the short gap
    line: int


class Pause(BaseModel):
    kind: Literal["pause"] = "pause"
    seconds: float
    line: int


class MusicUp(BaseModel):
    kind: Literal["music_up"] = "music_up"
    seconds: float
    line: int


Item = Annotated[Union[Sentence, Pause, MusicUp], Field(discriminator="kind")]


class Chapter(BaseModel):
    index: int
    title: str | None  # None until named (no headings in script -> LLM names it)
    line: int | None = None
    items: list[Item]

    @property
    def sentences(self) -> list[Sentence]:
        return [i for i in self.items if isinstance(i, Sentence)]


class ScriptMeta(BaseModel):
    title: str | None = None
    background: BackgroundKind | None = None
    music: str | None = None


class Script(BaseModel):
    meta: ScriptMeta
    chapters: list[Chapter]
    has_headings: bool  # False -> chapters must be split by the LLM
    warnings: list[ScriptIssue] = Field(default_factory=list)

    @property
    def sentences(self) -> list[Sentence]:
        return [s for c in self.chapters for s in c.sentences]

    @property
    def speakers(self) -> list[str | None]:
        seen: dict[str | None, None] = {}
        for s in self.sentences:
            seen.setdefault(s.speaker, None)
        return list(seen)

    def full_text(self) -> str:
        return " ".join(s.text for s in self.sentences)
