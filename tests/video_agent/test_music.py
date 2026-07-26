import subprocess
from pathlib import Path
from video_agent.music import mix_music_under_voice


def test_skips_when_no_track_for_region(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"")          # placeholder; mock subprocess
    out = tmp_path / "out.mp3"
    # No music dir created → nothing to mix; should return voice path unchanged
    result = mix_music_under_voice(voice, out, region="atlantis",
                                    music_root=tmp_path / "no_music")
    assert result == voice


def test_runs_ffmpeg_when_track_exists(tmp_path, monkeypatch):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x" * 32)
    music_root = tmp_path / "music"
    music_root.mkdir()
    track = music_root / "australia.mp3"
    track.write_bytes(b"y" * 32)
    out = tmp_path / "out.mp3"

    calls = []
    def fake_run(cmd, check):
        calls.append(cmd)
        out.write_bytes(b"mixed")
        class R: returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = mix_music_under_voice(voice, out, region="australia",
                                    music_root=music_root)
    assert result == out
    assert "sidechaincompress" in " ".join(calls[0])
