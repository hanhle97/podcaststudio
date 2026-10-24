import io
import json
import wave

import httpx
import pytest

import app.tts.voice_studio as vs
from app.tts.base import TTSError, TTSRequest
from app.tts.voice_studio import VoiceStudioProvider, normalize_base_url


def wav_bytes(seconds=0.1, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return buf.getvalue()


def audio_response():
    return httpx.Response(200, content=wav_bytes(), headers={"content-type": "audio/wav"})


def provider(handler, voice="v1", **kw):
    return VoiceStudioProvider("http://vs.test:3900", voice, backoff_base_s=0,
                               transport=httpx.MockTransport(handler), **kw)


def test_synthesize_sends_openai_payload():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(path=req.url.path, body=json.loads(req.content),
                    auth=req.headers.get("authorization"))
        return audio_response()

    res = provider(handler, api_key="secret").synthesize(
        TTSRequest(text="Xin chào", voice_id="v1", seed=42))
    assert res.format == "wav" and res.audio.startswith(b"RIFF")
    assert seen["path"] == "/v1/audio/speech" and seen["auth"] == "Bearer secret"
    assert seen["body"] == {"model": "omnivoice", "input": "Xin chào", "voice": "v1",
                            "response_format": "wav", "language": "vi", "speed": 1.0,
                            "seed": 42}


def test_retries_on_503_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, json={"detail": "busy"}, headers={"retry-after": "0"})
        return audio_response()

    assert provider(handler).synthesize(TTSRequest(text="a", voice_id="v1")).audio
    assert len(calls) == 3


def test_client_error_is_not_retried():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "bad voice"}})

    with pytest.raises(TTSError, match="bad voice"):
        provider(handler).synthesize(TTSRequest(text="a", voice_id="v1"))
    assert len(calls) == 1


def test_network_failure_exhausts_retries():
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(TTSError, match="không phản hồi") as exc:
        provider(handler, max_retries=2).synthesize(TTSRequest(text="a", voice_id="v1"))
    assert exc.value.retryable and len(calls) == 3


def test_rejects_non_audio_and_too_long_text():
    p = provider(lambda r: httpx.Response(200, json={"job_id": "x"}))
    with pytest.raises(TTSError, match="thay vì audio"):
        p.synthesize(TTSRequest(text="a", voice_id="v1"))
    with pytest.raises(TTSError, match="4096"):
        p.synthesize(TTSRequest(text="a" * 5000, voice_id="v1"))


def test_health_checks_voice():
    def handler(req):
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "Tôi"}]})

    assert provider(handler).health()["ok"]
    assert not provider(handler, voice="missing").health()["ok"]

    def down(req):
        raise httpx.ConnectError("x")

    assert "không phản hồi" in provider(down).health()["message"]


def test_cache_key_depends_on_audio_affecting_fields():
    p = provider(lambda r: httpx.Response(500))
    base = TTSRequest(text="a", voice_id="v1", seed=1)
    assert p.cache_key(base) == p.cache_key(TTSRequest(text="a", voice_id="v1", seed=1))
    assert p.cache_key(base) != p.cache_key(TTSRequest(text="b", voice_id="v1", seed=1))
    assert p.cache_key(base) != p.cache_key(TTSRequest(text="a", voice_id="v1", seed=2))
    assert p.cache_key(base) != p.cache_key(TTSRequest(text="a", voice_id="v2", seed=1))


def test_normalize_base_url(monkeypatch):
    monkeypatch.setattr(vs, "in_container", lambda: True)
    assert normalize_base_url("http://localhost:3900/") == "http://host.docker.internal:3900"
    assert normalize_base_url("192.168.1.5:3900") == "http://192.168.1.5:3900"
    monkeypatch.setattr(vs, "in_container", lambda: False)
    assert normalize_base_url("http://host.docker.internal:3900") == "http://localhost:3900"
