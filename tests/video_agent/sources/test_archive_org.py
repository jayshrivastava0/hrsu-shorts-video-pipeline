import responses
from video_agent.sources.archive_org import ArchiveOrgSource, _parse_runtime

_API = "https://archive.org/advancedsearch.php"


@responses.activate
def test_returns_clip_candidates():
    responses.add(responses.GET, _API, json={
        "response": {"docs": [
            {"identifier": "prelinger-calcium-film", "title": "Calcium Compounds",
             "description": "Industrial chemistry educational film.", "runtime": "12:30"},
        ]}
    })
    src = ArchiveOrgSource()
    cands = src.search("calcium nitrate industrial", limit=1)
    assert len(cands) == 1
    c = cands[0]
    assert c.source == "archive_org"
    assert c.is_clip is True
    assert "archive.org/details/prelinger-calcium-film" in c.url
    assert "Calcium Compounds" in c.caption
    assert c.duration_s == pytest.approx(750.0)  # 12*60+30


@responses.activate
def test_empty_response_returns_nothing():
    responses.add(responses.GET, _API, json={"response": {"docs": []}})
    assert ArchiveOrgSource().search("xyz", limit=3) == []


@responses.activate
def test_network_error_returns_empty():
    responses.add(responses.GET, _API, body=Exception("timeout"))
    assert ArchiveOrgSource().search("calcium", limit=3) == []


def test_parse_runtime_mmss():
    assert _parse_runtime("12:30") == pytest.approx(750.0)


def test_parse_runtime_hhmmss():
    assert _parse_runtime("1:02:03") == pytest.approx(3723.0)


def test_parse_runtime_none():
    assert _parse_runtime(None) is None


def test_parse_runtime_list():
    assert _parse_runtime(["05:00"]) == pytest.approx(300.0)


import pytest
