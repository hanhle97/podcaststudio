"""Probe the Voice Studio API: health, voice lookup, one short Vietnamese sentence.

Usage (from media_service/):
    python scripts/probe_voice_studio.py [--text "..."] [--out probe.wav]

Reads VOICE_STUDIO_URL / VOICE_STUDIO_API_KEY / VOICE_STUDIO_VOICE_ID from the
environment or the repo's .env.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import wave
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.tts.voice_studio import normalize_base_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="Xin chào các bạn, đây là bản thử giọng của mình. "
                                          "Người ở đâu?")
    parser.add_argument("--out", default="probe_voice_studio.wav")
    args = parser.parse_args()

    s = get_settings()
    base = normalize_base_url(s.voice_studio_url)
    headers = {"Authorization": f"Bearer {s.voice_studio_api_key}"} \
        if s.voice_studio_api_key else {}
    print(f"Voice Studio: {base}  (voice_id={s.voice_studio_voice_id or '<chưa đặt>'})")

    with httpx.Client(base_url=base, headers=headers, timeout=300) as client:
        try:
            health = client.get("/health", timeout=10)
        except httpx.HTTPError as e:
            print(f"✗ Không kết nối được Voice Studio: {e}")
            return 1
        print(f"✓ /health {health.status_code}: {health.text.strip()}")

        voices = client.get("/v1/audio/voices", timeout=30).json().get("voices", [])
        own = [v for v in voices if v.get("type") != "openai_alias"]
        print(f"✓ {len(voices)} giọng, trong đó không phải alias: "
              + ", ".join(f"{v['voice_id']} ({v.get('name')})" for v in own))
        if s.voice_studio_voice_id and not any(v["voice_id"] == s.voice_studio_voice_id
                                               for v in voices):
            print(f"✗ Không thấy voice_id {s.voice_studio_voice_id} trong danh sách.")
            return 1

        payload = {"model": "omnivoice", "input": args.text,
                   "voice": s.voice_studio_voice_id or "default",
                   "response_format": "wav", "language": "vi"}
        t0 = time.perf_counter()
        r = client.post("/v1/audio/speech", json=payload)
        elapsed = time.perf_counter() - t0
        if r.status_code != 200:
            print(f"✗ /v1/audio/speech {r.status_code}: {r.text[:500]}")
            return 1

    Path(args.out).write_bytes(r.content)
    with wave.open(io.BytesIO(r.content)) as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        duration = w.getnframes() / rate
    print(f"✓ /v1/audio/speech {r.headers.get('content-type')} {len(r.content)} bytes "
          f"trong {elapsed:.1f}s")
    print(f"  Lưu: {Path(args.out).resolve()}")
    print(f"  Thời lượng: {duration:.2f}s | sample rate: {rate} Hz | kênh: {channels} | "
          f"{width * 8}-bit")
    extra = {k: v for k, v in r.headers.items() if k.lower().startswith("x-")}
    if extra:
        print(f"  Header: {extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
