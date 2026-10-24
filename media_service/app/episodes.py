"""Episode storage: everything for one episode lives in data/episodes/{id}/."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import BrandConfig, Settings
from app.script import Script, ScriptIssue, ScriptParseError, parse_script

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv"}


class EpisodeNotFound(Exception):
    pass


class EpisodeInfo(BaseModel):
    id: str
    title: str | None
    source_filename: str | None
    script_sha256: str
    created_at: datetime
    warnings: list[ScriptIssue] = Field(default_factory=list)


class EpisodePaths:
    """All well-known file locations of an episode."""

    def __init__(self, root: Path):
        self.root = root
        self.script_md = root / "script.md"
        self.script_json = root / "script.json"
        self.episode_json = root / "episode.json"
        self.stamps = root / ".stamps"


def slugify(text: str, max_len: int = 40) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "episode"


def list_assets(folder: Path, extensions: set[str]) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in extensions)


class EpisodeStore:
    def __init__(self, settings: Settings, brand: BrandConfig):
        self.settings = settings
        self.brand = brand
        self.root = settings.episodes_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def paths(self, episode_id: str) -> EpisodePaths:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", episode_id):
            raise EpisodeNotFound(episode_id)
        return EpisodePaths(self.root / episode_id)

    def create(self, text: str, filename: str | None = None) -> tuple[EpisodeInfo, Script]:
        """Parse + validate a script and create (or reuse) its episode folder.

        The id is derived from the script content, so uploading the same script
        twice yields the same episode and finished steps are reused.
        """
        script = parse_script(text, self.brand.script)
        self._validate_assets(script, text)

        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = next(self.root.glob(f"*-{sha[:8]}"), None)
        if existing and existing.is_dir():
            episode_id = existing.name
        else:
            seed = script.meta.title or " ".join(script.full_text().split()[:6])
            episode_id = f"{slugify(seed)}-{sha[:8]}"

        paths = EpisodePaths(self.root / episode_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.script_md.write_text(text, encoding="utf-8", newline="")
        paths.script_json.write_text(script.model_dump_json(indent=2), encoding="utf-8")

        created = datetime.now(timezone.utc)
        if paths.episode_json.exists():
            created = EpisodeInfo.model_validate_json(
                paths.episode_json.read_text(encoding="utf-8")).created_at
        info = EpisodeInfo(id=episode_id, title=script.meta.title, source_filename=filename,
                           script_sha256=sha, created_at=created, warnings=script.warnings)
        paths.episode_json.write_text(info.model_dump_json(indent=2), encoding="utf-8")
        return info, script

    def _validate_assets(self, script: Script, text: str) -> None:
        music = script.meta.music
        if music:
            available = list_assets(self.settings.music_dir, AUDIO_EXTENSIONS)
            if not (self.settings.music_dir / music).is_file():
                names = ", ".join(p.name for p in available) or "(thư mục đang trống)"
                line = next((i + 1 for i, ln in enumerate(text.splitlines())
                             if re.match(r"^\s*music\s*:", ln)), None)
                raise ScriptParseError([ScriptIssue(
                    line=line, message=f"Không tìm thấy bài nhạc '{music}' trong assets/music/.",
                    hint=f"Các bài hiện có: {names}. Hoặc xóa dòng music để chọn ngẫu nhiên.",
                )])
        background = script.meta.background or self.brand.video.background
        if background == "kenburns" and not list_assets(self.settings.backgrounds_dir,
                                                       IMAGE_EXTENSIONS):
            script.warnings.append(ScriptIssue(
                severity="warning", message="Nền 'kenburns' cần ảnh trong assets/backgrounds/; "
                "chưa có ảnh nên sẽ dùng nền gradient."))
        if background == "loop" and not list_assets(self.settings.backgrounds_dir,
                                                   VIDEO_EXTENSIONS):
            script.warnings.append(ScriptIssue(
                severity="warning", message="Nền 'loop' cần video trong assets/backgrounds/; "
                "chưa có video nên sẽ dùng nền gradient."))

    def get(self, episode_id: str) -> EpisodeInfo:
        paths = self.paths(episode_id)
        if not paths.episode_json.exists():
            raise EpisodeNotFound(episode_id)
        return EpisodeInfo.model_validate_json(paths.episode_json.read_text(encoding="utf-8"))

    def load_script(self, episode_id: str) -> Script:
        return Script.model_validate_json(
            self.paths(episode_id).script_json.read_text(encoding="utf-8"))

    def list(self) -> list[EpisodeInfo]:
        infos = []
        for d in self.root.iterdir():
            if (d / "episode.json").exists():
                infos.append(self.get(d.name))
        return sorted(infos, key=lambda i: i.created_at, reverse=True)

    def files(self, episode_id: str) -> list[dict]:
        paths = self.paths(episode_id)
        if not paths.root.is_dir():
            raise EpisodeNotFound(episode_id)
        out = []
        for p in sorted(paths.root.rglob("*")):
            rel = p.relative_to(paths.root)
            if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                st = p.stat()
                out.append({
                    "path": rel.as_posix(), "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                })
        return out


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
