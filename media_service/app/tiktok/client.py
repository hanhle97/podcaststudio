"""TikTok Content Posting API with OAuth v2: inbox drafts (default) or Direct Post.

Docs: developers.tiktok.com/doc/content-posting-api-get-started,
      /doc/content-posting-api-reference-upload-video (inbox drafts),
      /doc/content-posting-api-media-transfer-guide, /doc/login-kit-web,
      /doc/oauth-user-access-token-management
Unaudited apps may Direct Post only to private accounts, so the default mode sends
drafts to the creator's TikTok inbox (scope video.upload); the creator posts in the app.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = {"inbox": "user.info.basic,video.upload", "direct": "user.info.basic,video.publish"}
DONE_STATUSES = {"inbox": {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"},
                 "direct": {"PUBLISH_COMPLETE"}}
POLL_SECONDS = 10  # status/init endpoints are rate limited per token

MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


class TikTokError(Exception):
    pass


def chunk_plan(size: int, chunk: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) following the media transfer rules.

    Files up to 64 MB go in one piece (chunk_size = file size; TikTok rejects a
    chunk_size larger than the video). Bigger files use `chunk`-sized pieces with
    count = floor(size / chunk); the last piece absorbs the remainder.
    """
    if size <= 0:
        raise TikTokError("File video rỗng.")
    if size <= MAX_CHUNK:
        return size, 1
    chunk = max(MIN_CHUNK, min(chunk, MAX_CHUNK))
    return chunk, max(1, size // chunk)


def chunk_ranges(size: int, chunk: int, count: int) -> list[tuple[int, int]]:
    """Inclusive byte ranges; the last range runs to the end of the file."""
    ranges = [(i * chunk, (i + 1) * chunk - 1) for i in range(count)]
    ranges[-1] = (ranges[-1][0], size - 1)
    return ranges


@dataclass
class Token:
    access_token: str
    refresh_token: str
    open_id: str
    expires_at: float
    refresh_expires_at: float
    scope: str = ""

    @classmethod
    def from_response(cls, data: dict[str, Any], now: float) -> Token:
        return cls(access_token=data["access_token"], refresh_token=data["refresh_token"],
                   open_id=data.get("open_id", ""), scope=data.get("scope", ""),
                   expires_at=now + float(data.get("expires_in", 86400)),
                   refresh_expires_at=now + float(data.get("refresh_expires_in", 31536000)))


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Token | None:
        if not self.path.exists():
            return None
        return Token(**json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(token.__dict__, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class TikTokClient:
    def __init__(self, client_key: str, client_secret: str, redirect_uri: str, store: TokenStore,
                 mode: str = "inbox", transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        if mode not in SCOPES:
            raise TikTokError(f"TIKTOK_MODE phải là inbox hoặc direct, không phải {mode!r}.")
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.store = store
        self.mode = mode
        self.clock = clock
        self.sleep = sleep
        self._http = httpx.Client(timeout=httpx.Timeout(120, connect=15), transport=transport)

    # --- OAuth ---------------------------------------------------------------------------

    def configured(self) -> bool:
        return bool(self.client_key and self.client_secret and self.redirect_uri)

    def authorize_url(self, state: str) -> str:
        if not self.configured():
            raise TikTokError("Chưa cấu hình TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / "
                              "TIKTOK_REDIRECT_URI trong .env.")
        query = {"client_key": self.client_key, "response_type": "code",
                 "scope": SCOPES[self.mode], "redirect_uri": self.redirect_uri, "state": state}
        return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"

    def missing_scope(self) -> str | None:
        """The scope this mode needs but the stored token lacks (re-authorize)."""
        token = self.store.load()
        need = SCOPES[self.mode].split(",")[1]
        if token and token.scope and need not in token.scope.split(","):
            return need
        return None

    def exchange_code(self, code: str) -> Token:
        token = self._token_request({"code": urllib.parse.unquote(code),
                                     "grant_type": "authorization_code",
                                     "redirect_uri": self.redirect_uri})
        self.store.save(token)
        return token

    def access_token(self) -> Token:
        token = self.store.load()
        if not token:
            raise TikTokError("Chưa kết nối tài khoản TikTok. Mở /tiktok/auth/start để đăng nhập.")
        if token.expires_at - self.clock() > 300:
            return token
        if token.refresh_expires_at <= self.clock():
            raise TikTokError("Phiên TikTok đã hết hạn (365 ngày). Hãy kết nối lại.")
        fresh = self._token_request({"grant_type": "refresh_token",
                                     "refresh_token": token.refresh_token})
        fresh.open_id = fresh.open_id or token.open_id
        fresh.scope = fresh.scope or token.scope
        self.store.save(fresh)
        return fresh

    def _token_request(self, params: dict[str, str]) -> Token:
        body = {"client_key": self.client_key, "client_secret": self.client_secret, **params}
        r = self._http.post(f"{API}/v2/oauth/token/", data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = _json(r)
        if r.status_code != 200 or "access_token" not in data:
            raise TikTokError(f"TikTok từ chối token: {data.get('error_description') or data}")
        return Token.from_response(data, self.clock())

    # --- API -----------------------------------------------------------------------------

    def _api(self, path: str, body: dict[str, Any] | None = None,
             method: str = "POST") -> dict[str, Any]:
        token = self.access_token()
        headers = {"Authorization": f"Bearer {token.access_token}",
                   "Content-Type": "application/json; charset=UTF-8"}
        if method == "GET":
            r = self._http.get(f"{API}{path}", headers=headers)
        else:
            r = self._http.post(f"{API}{path}", json=body or {}, headers=headers)
        data = _json(r)
        err = data.get("error") or {}
        if r.status_code != 200 or err.get("code", "ok") != "ok":
            raise TikTokError(f"TikTok API {path} lỗi ({r.status_code}): "
                              f"{err.get('code')} {err.get('message') or data}")
        return data.get("data") or {}

    def creator_info(self) -> dict[str, Any]:
        """Direct Post only: allowed privacy levels, max duration, username."""
        return self._api("/v2/post/publish/creator_info/query/", {})

    def user_info(self) -> dict[str, Any]:
        """Display name of the connected account (scope user.info.basic)."""
        data = self._api("/v2/user/info/?fields=open_id,display_name", method="GET")
        return data.get("user") or {}

    def upload_to_inbox(self, path: Path, on_progress: Callable[[str], None] | None = None,
                        timeout_s: float = 600) -> dict[str, Any]:
        """Send one video to the creator's TikTok inbox as a draft."""
        return self._upload("/v2/post/publish/inbox/video/init/", {}, path,
                            DONE_STATUSES["inbox"], on_progress, timeout_s)

    def publish_video(self, path: Path, title: str, privacy_level: str,
                      on_progress: Callable[[str], None] | None = None,
                      timeout_s: float = 600) -> dict[str, Any]:
        """Direct-post one video; returns {publish_id, status, post_id?}."""
        post_info = {"title": title[:2200], "privacy_level": privacy_level,
                     "disable_duet": False, "disable_comment": False,
                     "disable_stitch": False, "video_cover_timestamp_ms": 1500}
        return self._upload("/v2/post/publish/video/init/", {"post_info": post_info}, path,
                            DONE_STATUSES["direct"], on_progress, timeout_s)

    def _upload(self, init_path: str, extra: dict[str, Any], path: Path, done: set[str],
                on_progress: Callable[[str], None] | None, timeout_s: float) -> dict[str, Any]:
        size = path.stat().st_size
        chunk, count = chunk_plan(size)
        init = self._api(init_path, {**extra, "source_info": {
            "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
            "total_chunk_count": count}})
        upload_url, publish_id = init["upload_url"], init["publish_id"]
        with path.open("rb") as f:
            for i, (first, last) in enumerate(chunk_ranges(size, chunk, count), 1):
                f.seek(first)
                data = f.read(last - first + 1)
                r = self._http.put(upload_url, content=data, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise TikTokError(f"Upload chunk {i}/{count} lỗi HTTP {r.status_code}: "
                                      f"{r.text[:200]}")
                if on_progress:
                    on_progress(f"Đã upload {i}/{count} phần")
        return self.wait_status(publish_id, done, timeout_s, on_progress)

    def wait_status(self, publish_id: str, done: set[str], timeout_s: float,
                    on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        deadline = self.clock() + timeout_s
        while True:
            data = self._api("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status", "")
            if status in done:
                ids = data.get("publicaly_available_post_id") or data.get(
                    "publicly_available_post_id") or []
                return {"publish_id": publish_id, "status": status,
                        "post_id": str(ids[0]) if ids else None}
            if status == "FAILED":
                raise TikTokError(f"TikTok xử lý video thất bại: {data.get('fail_reason')}")
            if self.clock() > deadline:
                raise TikTokError(f"TikTok chưa xử lý xong sau {timeout_s:.0f}s "
                                  f"(trạng thái {status}).")
            if on_progress:
                on_progress(f"TikTok đang xử lý ({status})")
            self.sleep(POLL_SECONDS)


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {"raw": r.text[:300]}
    return data if isinstance(data, dict) else {"data": data}
