import socket
import ssl
import urllib.error

import pytest

from fakes import FakeTransport, ok_json
from wikitrends import DEFAULT_CONTACT, VERSION
from wikitrends import thresholds as T
from wikitrends.deadline import Deadline, DeadlineReached
from wikitrends.errors import WikitrendsError
from wikitrends.http import HttpClient, Response, classify_network_error

URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/x"


def make_client(transport, **kwargs):
    sleeps = []
    kwargs.setdefault("rate_per_minute", None)
    client = HttpClient(transport, sleep=sleeps.append, **kwargs)
    return client, sleeps


def test_requests_are_spaced_by_the_rate_limit():
    now = [100.0]
    transport = FakeTransport({URL: [ok_json("{}")] * 3})
    client, sleeps = make_client(transport, rate_per_minute=120, clock=lambda: now[0])
    for _ in range(3):
        client.get(URL)
    assert sleeps == [0.5, 1.0]


def test_get_action_without_maxlag():
    endpoint = "https://www.wikidata.org/w/api.php"
    expected = endpoint + "?action=wbgetentities&format=json&formatversion=2&ids=Q1"
    transport = FakeTransport({expected: [ok_json('{"entities": {}}')]})
    client, _ = make_client(transport)
    assert client.get_action(endpoint, {"action": "wbgetentities", "ids": "Q1"}, maxlag=False) == {"entities": {}}


def test_get_action_rejects_api_error_before_callers_cache_it():
    endpoint = "https://uk.wikipedia.org/w/api.php"
    url = endpoint + "?action=query&format=json&formatversion=2&maxlag=5&titles=A"
    transport = FakeTransport({url: [ok_json('{"error": {"code": "badvalue", "info": "API error"}}')]})
    client, _ = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get_action(endpoint, {"action": "query", "titles": "A"})
    assert info.value.code == "network_error"


def tunnel_refused():
    return urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden"))


def dns_failure():
    return urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))


def cert_failure():
    return urllib.error.URLError(
        ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
    )


def test_user_agent_and_no_auth_by_default():
    transport = FakeTransport({URL: [ok_json("{}")]})
    client, _ = make_client(transport)
    client.get(URL)
    headers = transport.calls[0][1]
    assert headers["User-Agent"] == f"wikipedia-interest/{VERSION} ({DEFAULT_CONTACT})"
    assert "Authorization" not in headers


def test_contact_and_token_headers():
    transport = FakeTransport({URL: [ok_json("{}")]})
    client, _ = make_client(transport, contact="me@example.org", token="secret-token")
    client.get(URL)
    headers = transport.calls[0][1]
    assert headers["User-Agent"].endswith("(me@example.org)")
    assert headers["Authorization"] == "Bearer secret-token"


def test_retry_after_is_respected_on_429():
    transport = FakeTransport({URL: [ok_json("", 429, {"Retry-After": "3"}), ok_json('{"a": 1}')]})
    client, sleeps = make_client(transport)
    assert client.get(URL).json() == {"a": 1}
    assert sleeps == [3.0]


def test_5xx_exponential_backoff_then_network_error():
    transport = FakeTransport(default=ok_json("", 503))
    client, sleeps = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get(URL)
    assert info.value.code == "network_error"
    assert len(transport.calls) == T.MAX_RETRIES + 1
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_404_returned_without_retry():
    transport = FakeTransport({URL: [ok_json('{"detail": "not found"}', 404)]})
    client, sleeps = make_client(transport)
    assert client.get(URL).status == 404
    assert sleeps == []


def test_proxy_block_is_immediate_and_lists_domains():
    transport = FakeTransport(default=tunnel_refused())
    client, sleeps = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get(URL)
    assert info.value.code == "network_blocked"
    assert info.value.exit_code == 4
    assert info.value.details["domains"] == ["*.wikipedia.org", "wikimedia.org", "www.wikidata.org"]
    assert len(transport.calls) == 1
    assert sleeps == []


def test_dns_failure_retries_then_unavailable():
    transport = FakeTransport(default=dns_failure())
    client, sleeps = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get(URL)
    assert info.value.code == "network_unavailable"
    assert len(transport.calls) == T.MAX_RETRIES + 1


def test_tls_failure_is_immediate():
    transport = FakeTransport(default=cert_failure())
    client, _ = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get(URL)
    assert info.value.code == "tls_certificates_missing"
    assert "Install Certificates.command" in info.value.hint
    assert len(transport.calls) == 1


def test_transient_error_then_success():
    transport = FakeTransport({URL: [urllib.error.URLError(ConnectionResetError()), ok_json("{}")]})
    client, sleeps = make_client(transport)
    assert client.get(URL).status == 200
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    "exc, kind",
    [
        (cert_failure(), "tls"),
        (tunnel_refused(), "blocked"),
        (dns_failure(), "unavailable"),
        (urllib.error.URLError(ConnectionRefusedError()), "unavailable"),
        (socket.timeout("timed out"), "transient"),
    ],
)
def test_classify_network_error(exc, kind):
    assert classify_network_error(exc) == kind


def test_expired_deadline_blocks_new_requests():
    clock = [0.0]
    deadline = Deadline(1, clock=lambda: clock[0])
    clock[0] = 5.0
    transport = FakeTransport(default=ok_json("{}"))
    client, _ = make_client(transport, deadline=deadline)
    with pytest.raises(DeadlineReached):
        client.get(URL)
    assert transport.calls == []


def test_get_many_fetches_all_unique_urls_in_parallel_pool():
    urls = [f"{URL}/{i}" for i in range(20)]
    transport = FakeTransport(default=ok_json("{}"))
    client, _ = make_client(transport)
    results = client.get_many(urls + urls[:5])
    assert set(results) == set(urls)
    assert len(transport.calls) == 20
    assert client.requests_made == 20


def test_get_many_stops_starting_requests_after_deadline():
    clock = [0.0]
    deadline = Deadline(5, clock=lambda: clock[0])

    def advance(_url):
        clock[0] += 1.0

    transport = FakeTransport(default=ok_json("{}"), on_fetch=advance)
    client, _ = make_client(transport, deadline=deadline, parallelism=1)
    results = client.get_many([f"{URL}/{i}" for i in range(10)])
    assert len(results) == 5


def test_get_many_returns_non_fatal_errors_per_url():
    bad = f"{URL}/bad"
    transport = FakeTransport({bad: [ok_json("", 503)] * (T.MAX_RETRIES + 1)}, default=ok_json("{}"))
    client, _ = make_client(transport)
    results = client.get_many([f"{URL}/good", bad])
    assert isinstance(results[f"{URL}/good"], Response)
    assert isinstance(results[bad], WikitrendsError)
    assert results[bad].code == "network_error"


def test_get_many_raises_fatal_errors():
    transport = FakeTransport(default=tunnel_refused())
    client, _ = make_client(transport)
    with pytest.raises(WikitrendsError) as info:
        client.get_many([f"{URL}/{i}" for i in range(3)])
    assert info.value.code == "network_blocked"


def test_get_action_builds_deterministic_url_and_retries_maxlag():
    endpoint = "https://pl.wikipedia.org/w/api.php"
    expected = (
        endpoint
        + "?action=query&format=json&formatversion=2&maxlag=5&prop=pageprops&titles=Astronomia"
    )
    maxlag = ok_json('{"error": {"code": "maxlag", "info": "Waiting"}}', 200, {"Retry-After": "2"})
    transport = FakeTransport({expected: [maxlag, ok_json('{"query": {}}')]})
    client, sleeps = make_client(transport)
    data = client.get_action(endpoint, {"titles": "Astronomia", "action": "query", "prop": "pageprops"})
    assert data == {"query": {}}
    assert sleeps == [2.0]
