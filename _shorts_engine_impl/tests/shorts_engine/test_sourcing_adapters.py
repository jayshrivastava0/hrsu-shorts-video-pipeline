from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image


class TestOpenverse:
    def test_search_maps_results(self):
        from shorts_engine.sourcing.openverse import OpenverseSource
        fake = MagicMock()
        fake.status_code = 200
        fake.json.return_value = {"results": [
            {"url": "https://img.example/a.jpg", "width": 1920, "height": 1080,
             "title": "clarifier", "license": "cc0",
             "foreign_landing_url": "https://page.example/a"},
        ]}
        with patch("shorts_engine.sourcing.openverse.requests.get",
                   return_value=fake) as g:
            out = OpenverseSource().search("wastewater clarifier", limit=5)
        assert g.call_args.kwargs["params"]["license_type"] == "commercial"
        assert len(out) == 1
        c = out[0]
        assert (c.source, c.url, c.width) == ("openverse", "https://img.example/a.jpg", 1920)
        assert c.extra["license"] == "cc0"

    def test_network_error_returns_empty(self):
        from shorts_engine.sourcing.openverse import OpenverseSource
        with patch("shorts_engine.sourcing.openverse.requests.get",
                   side_effect=OSError("net down")):
            assert OpenverseSource().search("x") == []


class TestSearchTier:
    def test_round_robin_caps_at_per_tier_candidates(self, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate

        class Fake:
            def __init__(self, name, n):
                self.name, self.n = name, n
            def search(self, q, limit=4):
                return [RawCandidate(source=self.name, url=f"https://{self.name}/{i}",
                                     width=1600, height=900) for i in range(self.n)]

        monkeypatch.setattr(adapters, "tier_sources",
                            lambda tier: [Fake("s1", 6), Fake("s2", 6)])
        out = adapters.search_tier("api", "query")
        from shorts_engine import config
        assert len(out) == config.PER_TIER_CANDIDATES
        assert {c.source for c in out} == {"s1", "s2"}  # interleaved, not s1-only

    def test_failing_source_is_skipped(self, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate

        class Boom:
            name = "boom"
            def search(self, q, limit=4):
                raise RuntimeError("api down")
        class Ok:
            name = "ok"
            def search(self, q, limit=4):
                return [RawCandidate(source="ok", url="https://ok/1",
                                     width=1600, height=900)]

        monkeypatch.setattr(adapters, "tier_sources", lambda tier: [Boom(), Ok()])
        out = adapters.search_tier("api", "q")
        assert [c.source for c in out] == ["ok"]


class TestDownload:
    def test_download_writes_and_backfills_dimensions(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate
        img_bytes = tmp_path / "src.png"
        Image.new("RGB", (1400, 900), (10, 20, 30)).save(img_bytes)
        payload = img_bytes.read_bytes()

        fake = MagicMock()
        fake.status_code = 200
        fake.iter_content = lambda chunk_size: [payload]
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda *a: False
        monkeypatch.setattr(adapters.requests, "get", lambda *a, **k: fake)

        cand = RawCandidate(source="t", url="https://x.example/img.png", width=0, height=0)
        out = adapters.download(cand, tmp_path / "dl")
        assert out is not None and out.exists() and out.suffix == ".png"
        assert (cand.width, cand.height) == (1400, 900)

    def test_download_failure_returns_none(self, tmp_path, monkeypatch):
        from shorts_engine.sourcing import adapters
        from video_agent.sources.base import RawCandidate
        monkeypatch.setattr(adapters.requests, "get",
                            MagicMock(side_effect=OSError("refused")))
        cand = RawCandidate(source="t", url="https://x/y.jpg")
        assert adapters.download(cand, tmp_path) is None
