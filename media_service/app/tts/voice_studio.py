"""Voice Studio (OmniVoice) TTS provider.

API (see docs/api-auth.md and docs/generation-parameters.md in the Voice Studio repo):
    POST /generate          multipart form {text, profile_id, language, speed, seed, engine,
                            num_step, class_temperature, effect_preset, ...} -> WAV bytes.
                            Used instead of the OpenAI-style /v1/audio/speech because only the
                            native endpoint accepts the expressive sampling knobs.
    GET  /v1/audio/voices   -> {"voices": [{"voice_id","name","type"}]}
    GET  /health
Loopback calls need no auth; a configured OMNIVOICE_API_KEY is sent as Bearer.
Overload is signalled with 503/429 + Retry-After.
"""

from __future__ import annotations

import logging
import os
import random
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.tts.base import TTSError, TTSProvider, TTSRequest, TTSResult

log = logging.getLogger(__name__)

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DOCKER_HOST = "host.docker.internal"
MAX_INPUT_CHARS = 4096


def in_container() -> bool:
    return os.path.exists("/.dockerenv")


def normalize_base_url(url: str) -> str:
    """Make VOICE_STUDIO_URL work both inside Docker and on the host.

    Inside a container, localhost means the container itself, so it is rewritten
    to host.docker.internal; on the host the reverse rewrite applies.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return url
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    host = parts.hostname or ""
    if in_container() and host in _LOOPBACK:
        host = _DOCKER_HOST
    elif not in_container() and host == _DOCKER_HOST:
        host = "localhost"
    else:
        return url
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class VoiceStudioProvider(TTSProvider):
    name = "voice_studio"
    supports_word_timestamps = False

    def __init__(self, base_url: str, voice_id: str, api_key: str = "",
                 model: str = "omnivoice", timeout_s: float = 300, max_retries: int = 5,
                 backoff_base_s: float = 1.0, transport: httpx.BaseTransport | None = None):
        if not base_url:
            raise TTSError("Chưa cấu hình VOICE_STUDIO_URL trong .env.")
        self.base_url = normalize_base_url(base_url)
        self.voice_id = voice_id
        self.model = model
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=self.base_url, headers=headers,
                                    timeout=httpx.Timeout(timeout_s, connect=10),
                                    transport=transport)

    def cache_identity(self) -> dict[str, Any]:
        return {"model": self.model}

    # --- synthesis -------------------------------------------------------------------

    def synthesize(self, request: TTSRequest) -> TTSResult:
        if len(request.text) > MAX_INPUT_CHARS:
            raise TTSError(f"Đoạn text dài {len(request.text)} ký tự, vượt giới hạn "
                           f"{MAX_INPUT_CHARS} của Voice Studio.")
        form: dict[str, Any] = {
            "text": request.text, "profile_id": request.voice_id, "engine": self.model,
            "language": request.language, "speed": request.speed,
            # one request = one take; Voice Studio must not split and crossfade it itself
            "max_chunk_chars": 0, **request.options,
        }
        if request.seed is not None:
            form["seed"] = request.seed
        data = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in form.items()}
        response = self._request_with_retry("POST", "/generate", data=data)
        ctype = response.headers.get("content-type", "")
        if not ctype.startswith("audio/"):
            raise TTSError(f"Voice Studio trả về '{ctype}' thay vì audio: {response.text[:200]}")
        return TTSResult(audio=response.content, format="wav")

    def _request_with_retry(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error = ""
        for attempt in range(self.max_retries + 1):
            retry_after: float | None = None
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                last_error = f"hết thời gian chờ ({type(e).__name__})"
            except httpx.TransportError as e:
                last_error = f"không kết nối được ({e})"
            else:
                if response.status_code < 400:
                    return response
                detail = _error_message(response)
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}: {detail}"
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                else:
                    raise TTSError(f"Voice Studio từ chối yêu cầu (HTTP "
                                   f"{response.status_code}): {detail}")
            if attempt == self.max_retries:
                break
            delay = retry_after if retry_after is not None else \
                self.backoff_base_s * (2 ** attempt) * (0.8 + 0.4 * random.random())
            log.warning("Voice Studio %s, thử lại sau %.1fs (lần %d/%d)", last_error, delay,
                        attempt + 1, self.max_retries)
            time.sleep(min(delay, 60))
        raise TTSError(f"Voice Studio không phản hồi tại {self.base_url}: {last_error}",
                       retryable=True)

    # --- health -------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {"provider": self.name, "url": self.base_url,
                                "voice_id": self.voice_id}
        try:
            r = self._client.get("/health", timeout=10)
            r.raise_for_status()
            info["service"] = r.json()
            voices = self._client.get("/v1/audio/voices", timeout=30).json().get("voices", [])
        except (httpx.HTTPError, ValueError) as e:
            return {**info, "ok": False,
                    "message": f"Voice Studio không phản hồi tại {self.base_url} ({e}). "
                               "Hãy mở ứng dụng Voice Studio."}
        match = next((v for v in voices if v.get("voice_id") == self.voice_id), None)
        if not match:
            return {**info, "ok": False,
                    "message": f"Không tìm thấy giọng '{self.voice_id}' trong Voice Studio. "
                               "Kiểm tra VOICE_STUDIO_VOICE_ID."}
        return {**info, "ok": True, "voice_name": match.get("name"),
                "message": f"Voice Studio sẵn sàng, giọng '{match.get('name')}'."}


def _parse_retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if body.get("detail"):
            return str(body["detail"])
    return str(body)[:300]
