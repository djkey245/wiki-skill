"""Test doubles shared by tests."""
import threading

from wikitrends.http import Response


def ok_json(body: str, status: int = 200, headers=None) -> Response:
    return Response(status, dict(headers or {"Content-Type": "application/json"}), body.encode("utf-8"))


class FakeTransport:
    """Scripted transport: url -> list of Response | Exception, consumed in order.

    If `default` is given it answers any url whose script is empty.
    """

    def __init__(self, script=None, default=None, on_fetch=None):
        self.script = {url: list(items) for url, items in (script or {}).items()}
        self.default = default
        self.on_fetch = on_fetch
        self.calls = []
        self._lock = threading.Lock()

    def fetch(self, url, headers, timeout):
        with self._lock:
            self.calls.append((url, dict(headers)))
            items = self.script.get(url)
            item = items.pop(0) if items else self.default
        if self.on_fetch is not None:
            self.on_fetch(url)
        if item is None:
            raise AssertionError(f"unexpected request: {url}")
        if isinstance(item, BaseException):
            raise item
        return item
