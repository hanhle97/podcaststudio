"""Measure music ducking of an episode: music level under speech vs in breaks.

Usage:  python scripts/check_ducking.py <episode_dir>
Reads audio/voice.wav, audio/music_ducked.wav and segments.json (body timeline).
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np


def load(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
        ch, sr = w.getnchannels(), w.getframerate()
    return data.reshape(-1, ch).mean(axis=1) / 32768.0, sr


def rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12))


def region(x: np.ndarray, sr: int, start: float, end: float) -> np.ndarray:
    return x[int(start * sr): int(end * sr)]


def main() -> int:
    ep = Path(sys.argv[1])
    voice, sr = load(ep / "audio" / "voice.wav")
    music, _ = load(ep / "audio" / "music_ducked.wav")
    tl = json.loads((ep / "segments.json").read_text(encoding="utf-8"))

    speech_v, speech_m = [], []
    for s in tl["segments"]:
        if s["end"] - s["start"] > 1.5:  # inner part, away from attack/release
            speech_v.append(region(voice, sr, s["start"] + 0.5, s["end"] - 0.3))
            speech_m.append(region(music, sr, s["start"] + 0.5, s["end"] - 0.3))
    v_db = rms_db(np.concatenate(speech_v))
    m_db = rms_db(np.concatenate(speech_m))
    print(f"Giọng khi nói:              {v_db:6.1f} dBFS RMS")
    print(f"Nhạc dưới giọng:            {m_db:6.1f} dBFS RMS  ({m_db - v_db:+.1f} dB so với giọng)")
    for b in tl["breaks"]:
        dur = b["end"] - b["start"]
        tail = region(music, sr, b["end"] - min(0.5, dur / 2), b["end"])
        print(f"Nhạc cuối đoạn {b['kind']:<9} ({dur:.1f}s): {rms_db(tail):6.1f} dBFS RMS  "
              f"(lên {rms_db(tail) - m_db:+.1f} dB)")
    head = region(music, sr, 0, 0.3)
    print(f"Nhạc 0.3s đầu (fade in):    {rms_db(head):6.1f} dBFS RMS")
    print(f"Nhạc 0.5s cuối (fade out):  {rms_db(music[-sr // 2:]):6.1f} dBFS RMS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
