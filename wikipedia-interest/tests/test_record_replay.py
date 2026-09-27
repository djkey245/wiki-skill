import gzip

import pytest

from fakes import FakeTransport, ok_json
from wikitrends.http import (
    HttpClient,
    RecordingTransport,
    ReplayMissError,
    ReplayTransport,
    UrllibTransport,
    transport_from_env,
)

URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/x"


def test_record_then_replay(tmp_path):
    inner = FakeTransport({URL: [ok_json('{"items": [1]}')]})
    HttpClient(RecordingTransport(inner, tmp_path), token="secret-token").get(URL)

    replay = ReplayTransport(tmp_path)
    response = HttpClient(replay, sleep=lambda s: None).get(URL)
    assert response.status == 200
    assert response.json() == {"items": [1]}
    assert replay.misses == 0


def test_token_never_written_to_records(tmp_path):
    inner = FakeTransport({URL: [ok_json("{}")]})
    HttpClient(RecordingTransport(inner, tmp_path), token="secret-token").get(URL)
    raw = gzip.open(tmp_path / "records.jsonl.gz", "rt", encoding="utf-8").read()
    assert "secret-token" not in raw
    assert "Authorization" not in raw


def test_retryable_statuses_not_recorded(tmp_path):
    inner = FakeTransport({URL: [ok_json("", 503), ok_json("{}")]})
    HttpClient(RecordingTransport(inner, tmp_path), sleep=lambda s: None).get(URL)
    lines = gzip.open(tmp_path / "records.jsonl.gz", "rt", encoding="utf-8").read().splitlines()
    assert len(lines) == 1
    assert '"status": 200' in lines[0]


def test_replay_miss_is_an_error(tmp_path):
    with pytest.raises(ReplayMissError):
        ReplayTransport(tmp_path).fetch(URL, {}, 1.0)


def test_replay_or_record_appends_misses(tmp_path):
    fallback = FakeTransport({URL: [ok_json('{"n": 1}')]})
    first = ReplayTransport(tmp_path, fallback=fallback)
    assert first.fetch(URL, {}, 1.0).json() == {"n": 1}
    assert first.misses == 1

    second = ReplayTransport(tmp_path)
    assert second.fetch(URL, {}, 1.0).json() == {"n": 1}


def test_transport_from_env_modes(tmp_path):
    assert isinstance(transport_from_env({}), UrllibTransport)
    assert isinstance(transport_from_env({"WIKITRENDS_HTTP": f"record:{tmp_path}"}), RecordingTransport)
    replay = transport_from_env({"WIKITRENDS_HTTP": f"replay:{tmp_path}"})
    assert isinstance(replay, ReplayTransport) and replay.fallback is None
    both = transport_from_env({"WIKITRENDS_HTTP": f"replay-or-record:{tmp_path}"})
    assert isinstance(both, ReplayTransport) and isinstance(both.fallback, UrllibTransport)
    with pytest.raises(ValueError):
        transport_from_env({"WIKITRENDS_HTTP": "bogus:/x"})
    with pytest.raises(ValueError):
        transport_from_env({"WIKITRENDS_HTTP": "replay"})
