"""Pydantic schemas for every LLM output (also sent to the model as JSON Schema)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _clean(text: str) -> str:
    return " ".join(text.split())


class MetadataLLM(BaseModel):
    title: str = Field(min_length=5, max_length=70,
                       description="Tiêu đề YouTube tiếng Việt, hấp dẫn nhưng đúng sự thật")
    description: str = Field(min_length=80, max_length=3000,
                             description="Mô tả 2-4 đoạn ngắn, không gồm mục lục chapter")
    tags: list[str] = Field(min_length=10, max_length=15,
                            description="10-15 tag tiếng Việt (có thể có thuật ngữ gốc)")
    thumbnail_texts: list[str] = Field(min_length=3, max_length=3,
                                       description="3 phương án chữ trên thumbnail, mỗi cái 2-6 từ")

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out = [_clean(t).lstrip("#") for t in v if _clean(t)]
        if len({t.lower() for t in out}) != len(out):
            raise ValueError("tags bị trùng lặp")
        if any(len(t) > 40 for t in out):
            raise ValueError("mỗi tag tối đa 40 ký tự")
        return out

    @field_validator("thumbnail_texts")
    @classmethod
    def _thumbs(cls, v: list[str]) -> list[str]:
        out = [_clean(t) for t in v]
        if any(not t or len(t) > 40 for t in out):
            raise ValueError("mỗi text thumbnail phải có nội dung và tối đa 40 ký tự")
        return out


class ChapterLLM(BaseModel):
    title: str = Field(min_length=2, max_length=60)
    start_sentence_id: str = Field(description="id câu mở đầu chapter, ví dụ s0001")


class ChaptersLLM(BaseModel):
    chapters: list[ChapterLLM] = Field(min_length=1, max_length=12)


class ClipLLM(BaseModel):
    start_sentence_id: str = Field(description="id câu đầu tiên của clip")
    end_sentence_id: str = Field(description="id câu cuối cùng của clip")
    title: str = Field(min_length=2, max_length=60)
    hook: str = Field(min_length=2, max_length=80,
                      description="câu mồi ngắn hiện 2-3 giây đầu clip")
    reason: str = Field(min_length=5, max_length=300,
                        description="vì sao đoạn này tự đứng được và hấp dẫn")


class ClipsLLM(BaseModel):
    clips: list[ClipLLM] = Field(min_length=1, max_length=5)
