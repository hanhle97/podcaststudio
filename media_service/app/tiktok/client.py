"""TikTok Content Posting API (Direct Post, FILE_UPLOAD) with OAuth v2.

Docs: developers.tiktok.com/doc/content-posting-api-get-started,
      /doc/content-posting-api-media-transfer-guide, /doc/login-kit-web,
      /doc/oauth-user-access-token-management
Unaudited apps can only post SELF_ONLY (private) videos.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"
SCOPES = "user.info.basic,video.publish"

MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


class TikTokError(Exception):
    pass


def chunk_plan(size: int, chunk: int = DEFAULT_CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) following the media transfer rules.

    Under 5 MB the file goes whole; otherwise count = floor(size / chunk) and the
    last chunk absorbs the remainder.
    """
    if size <= 0:
        raise TikTokError("File video rỗng.")
    if size < MIN_CHUNK:
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
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.client_key = client_key
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.store = store
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
        query = {"client_key": self.client_key, "response_type": "code", "scope": SCOPES,
                 "redirect_uri": self.redirect_uri, "state": state}
        return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"

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

    def _api(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = self.access_token()
        r = self._http.post(f"{API}{path}", json=body,
                            headers={"Authorization": f"Bearer {token.access_token}",
                                     "Content-Type": "application/json; charset=UTF-8"})
        data = _json(r)
        err = data.get("error") or {}
        if r.status_code != 200 or err.get("code", "ok") != "ok":
            raise TikTokError(f"TikTok API {path} lỗi ({r.status_code}): "
                              f"{err.get('code')} {err.get('message') or data}")
        return data.get("data") or {}

    def creator_info(self) -> dict[str, Any]:
        return self._api("/v2/post/publish/creator_info/query/", {})

    def publish_video(self, path: Path, title: str, privacy_level: str,
                      on_progress: Callable[[str], None] | None = None,
                      timeout_s: float = 600) -> dict[str, Any]:
        """Direct-post one video; returns {publish_id, status, post_id?}."""
        size = path.stat().st_size
        chunk, count = chunk_plan(size)
        init = self._api("/v2/post/publish/video/init/", {
            "post_info": {"title": title[:2200], "privacy_level": privacy_level,
                          "disable_duet": False, "disable_comment": False,
                          "disable_stitch": False, "video_cover_timestamp_ms": 1500},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count},
        })
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
        return self.wait_published(publish_id, timeout_s, on_progress)

    def wait_published(self, publish_id: str, timeout_s: float,
                       on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
        deadline = self.clock() + timeout_s
        while True:
            data = self._api("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status", "")
            if status == "PUBLISH_COMPLETE":
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
            self.sleep(5)


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        return {"raw": r.text[:300]}
    return data if isinstance(data, dict) else {"data": data}
