"""data/inbox helpers used by the n8n workflow (it shares the same /data volume)."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

ARCHIVES = ("done", "rejected", "failed")
Archive = Literal["done", "rejected", "failed"]


class InboxError(Exception):
    pass


def inbox_dir(data_dir: Path) -> Path:
    return data_dir / "inbox"


def resolve(data_dir: Path, name: str) -> Path:
    """A script directly inside data/inbox, given its name or its /data/inbox/... path."""
    root = inbox_dir(data_dir).resolve()
    candidate = Path(name)
    if candidate.is_absolute() or name.startswith("/data/"):
        # n8n reports container paths (/data/inbox/x.md); keep only the file name
        candidate = Path(candidate.name)
    target = (root / candidate).resolve()
    if target.parent != root:
        raise InboxError(f"'{name}' không nằm trực tiếp trong data/inbox/.")
    if not target.is_file():
        raise InboxError(f"Không tìm thấy '{target.name}' trong data/inbox/.")
    return target


def archive(data_dir: Path, name: str, to: Archive) -> Path:
    """Move an inbox script to inbox/<to>/, never overwriting an older one."""
    if to not in ARCHIVES:
        raise InboxError(f"Thư mục đích phải là một trong {', '.join(ARCHIVES)}.")
    src = resolve(data_dir, name)
    dest_dir = inbox_dir(data_dir) / to
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = dest_dir / f"{src.stem}-{stamp}{src.suffix}"
    shutil.move(str(src), dest)
    return dest
