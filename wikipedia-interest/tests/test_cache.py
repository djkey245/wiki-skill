import sqlite3
import threading

import pytest

from wikitrends import thresholds as T
from wikitrends.cache import Cache

DAY = 24 * 3600
P, TITLE, AGENT = "pl.wikipedia", "Astronomia", "user"


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cache(tmp_path, clock):
    c = Cache(tmp_path / "cache.sqlite", clock=clock)
    yield c
    c.close()


def test_empty_cache_reports_everything_missing(cache):
    fresh, missing = cache.get_views(P, TITLE, AGENT, ["2024-01", "2024-02"])
    assert fresh == {}
    assert missing == ["2024-01", "2024-02"]


def test_immutable_months_cached_including_zeros(cache):
    cache.put_views(P, TITLE, AGENT, {"2024-01": 5}, ["2024-01", "2024-02"], window_end="2026-08")
    fresh, missing = cache.get_views(P, TITLE, AGENT, ["2024-01", "2024-02"])
    assert fresh == {"2024-01": 5, "2024-02": 0}
    assert missing == []


def test_provisional_zero_not_cached(cache):
    cache.put_views(P, TITLE, AGENT, {"2026-07": 10}, ["2026-07", "2026-08"], window_end="2026-08")
    fresh, missing = cache.get_views(P, TITLE, AGENT, ["2026-07", "2026-08"])
    assert fresh == {"2026-07": 10}
    assert missing == ["2026-08"]


def test_provisional_months_expire(cache, clock):
    cache.put_views(P, TITLE, AGENT, {"2026-07": 10, "2024-01": 3}, ["2026-07", "2024-01"], window_end="2026-08")
    clock.now += T.PROVISIONAL_TTL_SECONDS + 1
    fresh, missing = cache.get_views(P, TITLE, AGENT, ["2024-01", "2026-07"])
    assert fresh == {"2024-01": 3}
    assert missing == ["2026-07"]


def test_immutable_months_never_expire(cache, clock):
    cache.put_views(P, TITLE, AGENT, {"2024-01": 3}, ["2024-01"], window_end="2026-08")
    clock.now += 10_000 * DAY
    assert cache.get_views(P, TITLE, AGENT, ["2024-01"]) == ({"2024-01": 3}, [])


def test_not_found_cached_with_ttl(cache, clock):
    cache.put_not_found(P, "Typo_title", AGENT)
    assert cache.get_views(P, "Typo_title", AGENT, ["2024-01", "2024-02"]) == ({"2024-01": 0, "2024-02": 0}, [])
    clock.now += T.NOT_FOUND_TTL_SECONDS + 1
    assert cache.get_views(P, "Typo_title", AGENT, ["2024-01"]) == ({}, ["2024-01"])


def test_put_views_clears_not_found(cache):
    cache.put_not_found(P, TITLE, AGENT)
    cache.put_views(P, TITLE, AGENT, {"2024-01": 7}, ["2024-01"], window_end="2026-08")
    assert cache.get_views(P, TITLE, AGENT, ["2024-01"]) == ({"2024-01": 7}, [])


def test_project_views_revision_detection(cache):
    first = cache.put_project_views(P, AGENT, {"2024-01": 1000, "2026-08": 500}, window_end="2026-08")
    assert first == []
    assert cache.put_project_views(P, AGENT, {"2024-01": 1004, "2026-08": 900}, window_end="2026-08") == []
    revised = cache.put_project_views(P, AGENT, {"2024-01": 1020, "2026-08": 900}, window_end="2026-08")
    assert revised == ["2024-01"]


def test_invalidate_views_forces_refetch(cache):
    cache.put_views(P, TITLE, AGENT, {"2024-01": 5, "2024-02": 6}, ["2024-01", "2024-02"], window_end="2026-08")
    cache.invalidate_views(P, ["2024-02"])
    assert cache.get_views(P, TITLE, AGENT, ["2024-01", "2024-02"]) == ({"2024-01": 5}, ["2024-02"])


def test_invalidate_views_is_scoped_to_project(cache):
    cache.put_views("cs.wikipedia", TITLE, AGENT, {"2024-02": 6}, ["2024-02"], window_end="2026-08")
    cache.invalidate_views(P, ["2024-02"])
    assert cache.get_views("cs.wikipedia", TITLE, AGENT, ["2024-02"]) == ({"2024-02": 6}, [])


def test_meta_roundtrip_and_ttl(cache, clock):
    cache.put_meta("sitelinks", "Q333", {"pl": "Astronomia", "uk": "Астрономія"})
    assert cache.get_meta("sitelinks", "Q333") == {"pl": "Astronomia", "uk": "Астрономія"}
    assert cache.get_meta("sitelinks", "Q1") is None
    clock.now += T.METADATA_TTL_SECONDS + 1
    assert cache.get_meta("sitelinks", "Q333") is None


def test_info_counts_rows(cache):
    cache.put_views(P, TITLE, AGENT, {"2024-01": 5}, ["2024-01", "2024-02"], window_end="2026-08")
    cache.put_meta("labels", "Q1", "x")
    info = cache.info()
    assert info["rows"] == {"views": 2, "not_found": 0, "project_views": 0, "meta": 1}
    assert info["size_bytes"] > 0
    assert info["path"].endswith("cache.sqlite")


def test_clear_all_and_older_than(cache, clock):
    cache.put_meta("labels", "old", "x")
    clock.now += 100 * DAY
    cache.put_meta("labels", "new", "y", ttl=1000 * DAY)
    assert cache.clear(older_than_seconds=90 * DAY) == 1
    assert cache.get_meta("labels", "new") == "y"
    assert cache.clear() == 1
    assert cache.info()["rows"]["meta"] == 0


def test_parallel_writers_do_not_lock(tmp_path):
    path = tmp_path / "cache.sqlite"
    Cache(path).close()
    errors = []

    def writer(n):
        c = Cache(path)
        try:
            for i in range(100):
                c.put_views(P, f"T{n}_{i}", AGENT, {"2024-01": i}, ["2024-01"], window_end="2026-08")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    c = Cache(path)
    assert c.info()["rows"]["views"] == 400
    c.close()


def test_migration_drops_legacy_title_wide_404(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE not_found (project TEXT, title TEXT, agent TEXT, expires_at REAL, "
                     "fetched_at REAL, PRIMARY KEY (project, title, agent))")
        conn.execute("INSERT INTO not_found VALUES (?, ?, ?, ?, ?)",
                     (P, "Old title", AGENT, 999999.0, 0.0))
    migrated = Cache(path, clock=lambda: 1.0)
    assert migrated.get_views(P, "Old title", AGENT, ["2024-01"])[1] == ["2024-01"]
