"""Metadata (title, description, tags, thumbnail texts, chapters) and clip selection steps."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.episodes import write_json
from app.jobs import StepError
from app.llm.client import LLMClient, LLMError, generate, get_client
from app.llm.schemas import ChaptersLLM, ClipsLLM, MetadataLLM
from app.llm.timing import (
    chapters_from_llm, chapters_text, clips_from_llm, required_clip_count, transcript_lines,
    youtube_chapters,
)

if TYPE_CHECKING:
    from app.pipeline import StepEnv, StepProgress

SYSTEM = (
    "Bạn là biên tập viên YouTube/podcast tiếng Việt. Chỉ dựa vào nội dung kịch bản được "
    "cung cấp, không bịa thêm thông tin, số liệu hay lời hứa không có trong kịch bản. "
    "Viết tiếng Việt tự nhiên, có dấu đầy đủ. Trả lời đúng JSON schema yêu cầu."
)

METADATA_PROMPT = """Kịch bản tập podcast (mỗi dòng: [id câu] (thời điểm, độ dài) nội dung):

{transcript}

Hãy viết metadata cho video YouTube của tập này:
- title: {title_rule}
- description: 2-4 đoạn ngắn tóm tắt nội dung và lợi ích cho người xem; KHÔNG chèn mục lục,
  timestamp hay hashtag (hệ thống tự thêm).
- tags: 10-15 tag liên quan, ngắn gọn, không trùng nhau, không có dấu #.
- thumbnail_texts: đúng 3 phương án chữ to trên thumbnail, mỗi cái 2-6 từ, gây tò mò nhưng
  đúng nội dung."""

CHAPTERS_PROMPT = """Kịch bản tập podcast chưa chia chương (mỗi dòng: [id câu] (thời điểm, độ dài)
nội dung):

{transcript}

Hãy chia thành 3-8 chương theo mạch nội dung. Mỗi chương: title ngắn (2-6 từ) và
start_sentence_id là id câu mở đầu chương. Chương đầu tiên bắt đầu ở {first_id}. Mỗi chương
dài ít nhất 10 giây. Nếu cả tập quá ngắn, có thể ít chương hơn."""

CLIPS_PROMPT = """Kịch bản tập podcast (mỗi dòng: [id câu] (thời điểm, độ dài) nội dung), tổng
{duration:.0f} giây:

{transcript}

Chọn {count_rule} đoạn để cắt thành video Shorts dọc:
- Mỗi đoạn là một dải câu liên tiếp từ start_sentence_id đến end_sentence_id, tổng độ dài
  {min_s:g}-{max_s:g} giây (cộng độ dài các câu và khoảng nghỉ giữa chúng theo thời điểm).
- Đoạn phải tự đứng được: có mở ý rõ ràng và kết ý trọn vẹn, người chưa xem tập vẫn hiểu.
- Các đoạn không được chồng lên nhau.
- hook: câu mồi ngắn (tối đa 80 ký tự) hiện ở 2-3 giây đầu để giữ người xem.
- title: tiêu đề ngắn cho clip. reason: vì sao chọn đoạn này."""


def _load(env: StepEnv, name: str) -> Any:
    path = env.paths.root / name
    if not path.exists():
        raise StepError("metadata", f"Thiếu {name}. Hãy chạy các bước trước (mix).")
    return json.loads(path.read_text(encoding="utf-8"))


def _client(env: StepEnv, step: str) -> LLMClient:
    try:
        client = get_client(env.settings)
        check = getattr(client, "check", None)
        if check:
            check()
        return client
    except LLMError as e:
        raise StepError(step, str(e)) from e


def run_metadata(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    segments = _load(env, "segments.json")
    script = env.script
    client = _client(env, "metadata")
    try:
        if script.has_headings:
            chapters = [{"index": c["index"], "title": c["title"] or f"Phần {c['index'] + 1}",
                         "start": c["start"], "end": c["end"]} for c in segments["chapters"]]
        else:
            progress(0.05, "Kịch bản không có heading: nhờ LLM chia chương.")
            first_id = segments["segments"][0]["id"]
            chapters: list[dict[str, Any]] = []

            def check_chapters(result: ChaptersLLM) -> None:
                chapters[:] = chapters_from_llm(result, segments)

            generate(client, SYSTEM, CHAPTERS_PROMPT.format(
                transcript=transcript_lines(segments, []), first_id=first_id),
                ChaptersLLM, check_chapters)
        write_json(env.paths.root / "chapters.json", chapters)
        progress(0.4, f"{len(chapters)} chương. Đang viết tiêu đề, mô tả, tags…")

        title_rule = (f'dùng nguyên văn tiêu đề tác giả đã đặt: "{script.meta.title}"'
                      if script.meta.title else
                      "tối đa 70 ký tự, hấp dẫn nhưng không sai sự thật, không giật tít")
        meta, attempts = generate(client, SYSTEM, METADATA_PROMPT.format(
            transcript=transcript_lines(segments, chapters), title_rule=title_rule), MetadataLLM)
    except LLMError as e:
        raise StepError("metadata", str(e)) from e

    title = script.meta.title or meta.title
    toc = chapters_text(chapters)
    data = {
        "title": title,
        "description": f"{meta.description.strip()}\n\nMục lục:\n{toc}",
        "description_body": meta.description.strip(),
        "chapters": youtube_chapters(chapters),
        "chapters_text": toc,
        "tags": meta.tags,
        "thumbnail_texts": meta.thumbnail_texts,
        "language": "vi",
        "generated_by": {"provider": client.name, "model": client.model, "attempts": attempts},
    }
    write_json(env.paths.root / "metadata.json", data)
    return {"title": title, "chapters": len(chapters), "tags": len(meta.tags)}


def run_clips(env: StepEnv, progress: StepProgress) -> dict[str, Any]:
    segments = _load(env, "segments.json")
    chapters_file = env.paths.root / "chapters.json"
    chapters = json.loads(chapters_file.read_text(encoding="utf-8")) \
        if chapters_file.exists() else segments["chapters"]
    s = env.brand.shorts
    need = required_clip_count(segments["duration"], s.min_clips, s.min_seconds)
    count_rule = f"{need}-{s.max_clips}" if need < s.max_clips else str(need)
    client = _client(env, "clips")
    progress(0.1, f"Nhờ LLM chọn {count_rule} đoạn Shorts.")
    clips: list[dict[str, Any]] = []

    def check(result: ClipsLLM) -> None:
        clips[:] = clips_from_llm(result, segments, s.min_seconds, s.max_seconds, need)

    try:
        _, attempts = generate(client, SYSTEM, CLIPS_PROMPT.format(
            transcript=transcript_lines(segments, chapters), duration=segments["duration"],
            count_rule=count_rule, min_s=s.min_seconds, max_s=s.max_seconds), ClipsLLM, check)
    except LLMError as e:
        raise StepError("clips", str(e)) from e
    write_json(env.paths.root / "clips.json", {
        "clips": clips,
        "generated_by": {"provider": client.name, "model": client.model, "attempts": attempts},
    })
    return {"clips": len(clips), "attempts": attempts}
