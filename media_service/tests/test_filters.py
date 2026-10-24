import pytest

from app.audio.filters import (
    build_voice_filter, db_to_linear, loudnorm_apply, parse_loudnorm_json, trim_filter,
)
from app.config import AudioCfg, CompressorCfg, LoudnessCfg

FFMPEG_LOG = """
[Parsed_loudnorm_2 @ 0x55]
{
	"input_i" : "-23.41",
	"input_tp" : "-6.02",
	"input_lra" : "4.30",
	"input_thresh" : "-33.79",
	"output_i" : "-16.02",
	"output_tp" : "-1.50",
	"output_lra" : "3.90",
	"output_thresh" : "-26.37",
	"normalization_type" : "dynamic",
	"target_offset" : "0.02"
}
"""


def test_analysis_pass_defaults():
    f = build_voice_filter(AudioCfg())
    assert f == ("highpass=f=60,"
                 "acompressor=threshold=0.125893:ratio=2:attack=20:release=250,"
                 "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json")


def test_compressor_can_be_disabled_and_targets_configurable():
    cfg = AudioCfg(compressor=CompressorCfg(enabled=False), highpass_hz=80,
                   loudness=LoudnessCfg(target_lufs=-14, true_peak_db=-1, lra=9))
    f = build_voice_filter(cfg)
    assert "acompressor" not in f
    assert f.startswith("highpass=f=80,loudnorm=I=-14:TP=-1:LRA=9")


def test_second_pass_uses_measurements_and_resamples():
    measured = parse_loudnorm_json(FFMPEG_LOG)
    f = build_voice_filter(AudioCfg(), measured)
    assert ("measured_I=-23.41:measured_TP=-6.02:measured_LRA=4.30:"
            "measured_thresh=-33.79:offset=0.02:linear=true") in f
    assert f.endswith("aresample=48000,aformat=sample_fmts=s16:channel_layouts=mono")
    assert "print_format=json" not in f


def test_parse_loudnorm_takes_last_block_and_validates():
    measured = parse_loudnorm_json('noise {"x": 1}\n' + FFMPEG_LOG)
    assert measured["input_i"] == "-23.41"
    with pytest.raises(ValueError, match="Không tìm thấy"):
        parse_loudnorm_json("no json here")
    with pytest.raises(ValueError, match="im lặng"):
        parse_loudnorm_json(FFMPEG_LOG.replace('"-23.41"', '"-inf"'))


def test_loudnorm_apply_requires_all_fields():
    with pytest.raises(KeyError):
        loudnorm_apply(LoudnessCfg(), {"input_i": "-20"})


def test_trim_filter():
    f = trim_filter(-50)
    assert f.count("silenceremove") == 2 and f.count("areverse") == 2
    assert "start_threshold=-50dB" in f


def test_db_to_linear():
    assert db_to_linear(0) == 1
    assert round(db_to_linear(-20), 4) == 0.1
