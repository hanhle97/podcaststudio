from types import SimpleNamespace

import pytest

from app.audio.assemble import compute_timeline
from app.audio.filters import build_crossfade_graph, build_mix_graph, duck_ratio, music_gain_db
from app.audio.mix import pick_music
from app.config import AudioCfg, MusicCfg
from app.jobs import StepError
from app.script import parse_script


def test_music_gain_places_bed_relative_to_voice():
    # un-ducked bed at -16 - 22 + 10 = -28 LUFS; a -44.5 LUFS track needs +16.5 dB
    assert music_gain_db(-44.5, -16, MusicCfg()) == 16.5
    assert music_gain_db(-14, -16, MusicCfg(level_db=-25, duck_db=8)) == -19


def test_duck_ratio():
    assert duck_ratio(10) == 1.333
    assert duck_ratio(0) == 1.0
    assert duck_ratio(60) == 20.0
    assert duck_ratio(10, key_over_db=20) == 2.0


def test_mix_graph_structure():
    g = build_mix_graph(62.5, 16.5, MusicCfg(), 48000)
    assert g.startswith("[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        "volume=16.5dB,atrim=0:62.500,asetpts=N/SR/TB,afade=t=in:d=3,"
                        "afade=t=out:st=57.500:d=5[bed]")
    assert "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo," \
           "asplit=2[voice][key0]" in g
    assert "[bed][key]sidechaincompress=threshold=0.01:ratio=1.333:attack=20:release=1200" in g
    assert g.endswith("[voice][ducked]amix=inputs=2:duration=first:normalize=0[out]")


def test_mix_graph_music_stem_and_short_episode():
    g = build_mix_graph(3.0, 0, MusicCfg(fade_out_s=5, duck_release_ms=800), 44100, "music")
    assert "afade=t=out:st=0.000:d=5" in g
    assert "release=800" in g and "aresample=44100" in g
    assert g.endswith("[voice]anullsink;[ducked]anull[out]")


def test_crossfade_graph():
    assert build_crossfade_graph(1, 1, 48000).endswith("[out]")
    g = build_crossfade_graph(3, 1.5, 48000)
    assert "[a0][a1]acrossfade=d=1.5:c1=tri:c2=tri[x1]" in g
    assert g.endswith("[x1][a2]acrossfade=d=1.5:c1=tri:c2=tri[out]")


def test_timeline_shift_for_intro():
    s = parse_script("## A\nMột câu.\n\n## B\nHai câu.")
    tl = compute_timeline(s, {"s0001": 1.0, "s0002": 2.0}, AudioCfg())
    shifted = tl.shifted(3.0, tl.duration + 3.0 + 4.0)
    assert [x.start for x in shifted.segments] == [3.5, 5.7]
    assert shifted.chapters[0].start == 0.0          # intro belongs to the first chapter
    assert shifted.chapters[1].start == round(tl.chapters[1].start + 3, 3)
    assert shifted.chapters[-1].end == shifted.duration
    assert shifted.breaks[0].start == tl.breaks[0].start + 3


def env_for(tmp_path, episode_id="ep-1", music=None, cfg=None):
    return SimpleNamespace(
        episode_id=episode_id,
        brand=SimpleNamespace(audio=SimpleNamespace(music=cfg or MusicCfg())),
        settings=SimpleNamespace(music_dir=tmp_path),
        script=SimpleNamespace(meta=SimpleNamespace(music=music)),
    )


def test_pick_music_is_stable_per_episode(tmp_path):
    for name in ("a.mp3", "b.mp3", "c.wav", "intro.mp3", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    cfg = MusicCfg(intro="intro.mp3")
    picks = {pick_music(env_for(tmp_path, f"ep-{i}", cfg=cfg)).name for i in range(30)}
    assert picks <= {"a.mp3", "b.mp3", "c.wav"} and len(picks) > 1  # intro never used as bed
    first = pick_music(env_for(tmp_path, "ep-7", cfg=cfg))
    assert all(pick_music(env_for(tmp_path, "ep-7", cfg=cfg)) == first for _ in range(5))


def test_pick_music_frontmatter_disabled_and_empty(tmp_path):
    (tmp_path / "chosen.mp3").write_bytes(b"x")
    assert pick_music(env_for(tmp_path, music="chosen.mp3")).name == "chosen.mp3"
    with pytest.raises(StepError, match="missing.mp3"):
        pick_music(env_for(tmp_path, music="missing.mp3"))
    assert pick_music(env_for(tmp_path, cfg=MusicCfg(enabled=False))) is None
    assert pick_music(env_for(tmp_path / "nothing")) is None
