"""HTTP client: User-Agent, retries, network error classification, AQS pool, Action API etiquette, record/replay."""
from __future__ import annotations

import errno
import gzip
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from . import DEFAULT_CONTACT, VERSION
from . import thresholds as T
from .deadline import Deadline, DeadlineReached
from .errors import WikitrendsError

REQUIRED_DOMAINS = ["*.wikipedia.org", "wikimedia.org", "www.wikidata.org"]
FATAL_NETWORK_CODES = frozenset({"tls_certificates_missing", "network_blocked", "network_unavailable"})


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: bytes

    def header(self, name: str) -> Optional[str]:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class UrllibTransport:
    """Real network transport. Network failures propagate as URLError / OSError."""

    def fetch(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        request = urllib.request.Request(url, headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return Response(reply.status, dict(reply.headers.items()), reply.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read())


def classify_network_error(exc: BaseException) -> str:
    """Return 'tls', 'blocked', 'unavailable' or 'transient'."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return "tls"
    if "Tunnel connection failed" in text:
        return "blocked"
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
        return "unavailable"
    if isinstance(reason, OSError) and reason.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return "unavailable"
    return "transient"


def _backoff(attempt: int) -> float:
    return min(T.BACKOFF_MAX_SECONDS, T.BACKOFF_BASE_SECONDS * (2 ** attempt))


def _retry_delay(response: Response, attempt: int) -> float:
    value = response.header("Retry-After")
    if value is not None:
        try:
            return min(T.BACKOFF_MAX_SECONDS, max(0.0, float(value)))
        except ValueError:
            pass
    return _backoff(attempt)


def _tls_error() -> WikitrendsError:
    return WikitrendsError(
        "tls_certificates_missing",
        "TLS certificate verification failed (CERTIFICATE_VERIFY_FAILED)",
        hint="Run 'Install Certificates.command' from your Python.org install folder, "
        "or run the script via 'uv run'",
    )


def _blocked_error() -> WikitrendsError:
    return WikitrendsError(
        "network_blocked",
        "A proxy refused the connection to Wikimedia",
        hint="Ask the user to allow network access to: " + ", ".join(REQUIRED_DOMAINS),
        domains=list(REQUIRED_DOMAINS),
    )


def _unavailable_error() -> WikitrendsError:
    return WikitrendsError(
        "network_unavailable",
        "Cannot reach Wikimedia servers (DNS or connection failure)",
        hint="Check the internet connection, then repeat the command",
    )


def _network_error(url: str, detail: str) -> WikitrendsError:
    return WikitrendsError(
        "network_error",
        f"Request failed after retries: {detail}",
        hint="Repeat the same command once later",
        url=url,
    )


class HttpClient:
    def __init__(
        self,
        transport: Any,
        *,
        contact: Optional[str] = None,
        token: Optional[str] = None,
        deadline: Optional[Deadline] = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = T.MAX_RETRIES,
        parallelism: int = T.AQS_PARALLELISM,
        timeout: float = T.REQUEST_TIMEOUT_SECONDS,
        rate_per_minute: Optional[float] = T.RATE_LIMIT_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.contact = contact or DEFAULT_CONTACT
        self._token = token
        self.deadline = deadline
        self.sleep = sleep
        self.max_retries = max_retries
        self.parallelism = parallelism
        self.timeout = timeout
        self.requests_made = 0
        self._count_lock = threading.Lock()
        self.rate_per_minute = rate_per_minute
        self.clock = clock
        self._next_slot = 0.0
        self._rate_lock = threading.Lock()

    @classmethod
    def from_env(cls, env: Mapping[str, str], deadline: Optional[Deadline] = None) -> "HttpClient":
        replay_only = env.get("WIKITRENDS_HTTP", "").startswith("replay:")  # no network: no rate limit
        return cls(
            transport_from_env(env),
            contact=env.get("WIKITRENDS_CONTACT"),
            token=env.get("WIKIMEDIA_API_TOKEN"),
            deadline=deadline,
            rate_per_minute=None if replay_only else T.RATE_LIMIT_PER_MINUTE,
        )

    def headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": f"wikipedia-interest/{VERSION} ({self.contact})",
            "Accept": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _throttle(self) -> None:
        """Space request starts evenly across all client pools."""
        if not self.rate_per_minute:
            return
        with self._rate_lock:
            now = self.clock()
            start = max(now, self._next_slot)
            self._next_slot = start + 60.0 / self.rate_per_minute
        if start > now:
            self.sleep(start - now)

    def get(self, url: str) -> Response:
        attempt = 0
        while True:
            self._throttle()
            if self.deadline is not None:
                self.deadline.check()
            with self._count_lock:
                self.requests_made += 1
            try:
                response = self.transport.fetch(url, self.headers(), self.timeout)
            except OSError as exc:
                kind = classify_network_error(exc)
                if kind == "tls":
                    raise _tls_error() from exc
                if kind == "blocked":
                    raise _blocked_error() from exc
                if attempt >= self.max_retries:
                    if kind == "unavailable":
                        raise _unavailable_error() from exc
                    raise _network_error(url, str(exc)) from exc
                self.sleep(_backoff(attempt))
                attempt += 1
                continue
            if response.status == 429 or response.status >= 500:
                if attempt >= self.max_retries:
                    raise _network_error(url, f"HTTP {response.status}")
                self.sleep(_retry_delay(response, attempt))
                attempt += 1
                continue
            return response

    def get_many(self, urls: Iterable[str]) -> Dict[str, Union[Response, WikitrendsError]]:
        """AQS pool. URLs not started before the deadline are absent from the result."""
        unique = list(dict.fromkeys(urls))

        def work(url: str) -> Tuple[str, Optional[Union[Response, WikitrendsError]]]:
            try:
                return url, self.get(url)
            except DeadlineReached:
                return url, None
            except WikitrendsError as exc:
                if exc.code in FATAL_NETWORK_CODES:
                    raise
                return url, exc

        results: Dict[str, Union[Response, WikitrendsError]] = {}
        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            for url, result in pool.map(work, unique):
                if result is not None:
                    results[url] = result
        return results

    def get_action(
        self, endpoint: str, params: Mapping[str, str], *, maxlag: bool = True,
        allowed_errors: Sequence[str] = (),
    ) -> Any:
        """Sequential MediaWiki Action API / Wikidata request with maxlag handling."""
        query = dict(params)
        query.setdefault("format", "json")
        query.setdefault("formatversion", "2")
        if maxlag:
            query.setdefault("maxlag", str(T.MAXLAG_SECONDS))
        url = endpoint + "?" + urllib.parse.urlencode(sorted(query.items()))
        attempt = 0
        while True:
            response = self.get(url)
            data = response.json()
            error = data.get("error") if isinstance(data, dict) else None
            if error and error.get("code") == "maxlag":
                if attempt >= self.max_retries:
                    raise _network_error(url, "maxlag")
                self.sleep(_retry_delay(response, attempt))
                attempt += 1
                continue
            if error and error.get("code") not in allowed_errors:
                raise _network_error(url, f"Action API {error.get('code')}: {error.get('info', '')}")
            return data


RECORD_FILE = "records.jsonl.gz"
_KEPT_HEADERS = ("content-type", "retry-after")


class ReplayMissError(Exception):
    """A request absent from the replay corpus (contract tests must not touch the network)."""


def _is_recordable(response: Response) -> bool:
    return response.status != 429 and response.status < 500


class _Recorder:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / RECORD_FILE
        self._lock = threading.Lock()

    def append(self, url: str, response: Response) -> None:
        if not _is_recordable(response):
            return
        record = {
            "url": url,
            "status": response.status,
            "headers": {k: v for k, v in response.headers.items() if k.lower() in _KEPT_HEADERS},
            "body": response.body.decode("utf-8", "replace"),
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with gzip.open(self.path, "at", encoding="utf-8") as handle:
                handle.write(line)


def load_records(directory: Path) -> Dict[str, Response]:
    records: Dict[str, Response] = {}
    for path in sorted(Path(directory).glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                records[item["url"]] = Response(item["status"], item["headers"], item["body"].encode("utf-8"))
    return records


class RecordingTransport:
    def __init__(self, inner: Any, directory: Path) -> None:
        self.inner = inner
        self._recorder = _Recorder(directory)

    def fetch(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        response = self.inner.fetch(url, headers, timeout)
        self._recorder.append(url, response)
        return response


class ReplayTransport:
    def __init__(self, directory: Path, fallback: Any = None) -> None:
        self.records = load_records(directory) if Path(directory).exists() else {}
        self.fallback = fallback
        self._recorder = _Recorder(directory) if fallback is not None else None
        self.misses = 0
        self._lock = threading.Lock()

    def fetch(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        with self._lock:
            hit = self.records.get(url)
        if hit is not None:
            return hit
        if self.fallback is None:
            raise ReplayMissError(url)
        response = self.fallback.fetch(url, headers, timeout)
        assert self._recorder is not None
        self._recorder.append(url, response)
        with self._lock:
            self.misses += 1
            if _is_recordable(response):
                self.records[url] = response
        return response


def transport_from_env(env: Mapping[str, str]) -> Any:
    """WIKITRENDS_HTTP: unset | record:<dir> | replay:<dir> | replay-or-record:<dir>."""
    mode = env.get("WIKITRENDS_HTTP", "")
    if not mode:
        return UrllibTransport()
    kind, _, directory = mode.partition(":")
    if not directory:
        raise ValueError(f"WIKITRENDS_HTTP={mode!r}: expected <mode>:<directory>")
    path = Path(directory)
    if kind == "record":
        return RecordingTransport(UrllibTransport(), path)
    if kind == "replay":
        return ReplayTransport(path)
    if kind == "replay-or-record":
        return ReplayTransport(path, fallback=UrllibTransport())
    raise ValueError(f"WIKITRENDS_HTTP={mode!r}: unknown mode {kind!r}")
