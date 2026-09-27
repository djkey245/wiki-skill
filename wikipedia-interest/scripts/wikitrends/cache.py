"""SQLite cache (WAL): monthly views, 404s, project aggregates with revision check, metadata with TTL.

The cache is not analysis state: deleting it changes speed, never results (data §5).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from . import months
from . import thresholds as T

_SCHEMA = """
CREATE TABLE IF NOT EXISTS views (
    project TEXT NOT NULL, title TEXT NOT NULL, agent TEXT NOT NULL, month TEXT NOT NULL,
    views INTEGER NOT NULL, expires_at REAL, fetched_at REAL NOT NULL,
    PRIMARY KEY (project, title, agent, month)
);
CREATE TABLE IF NOT EXISTS not_found (
    project TEXT NOT NULL, title TEXT NOT NULL, agent TEXT NOT NULL,
    expires_at REAL NOT NULL, fetched_at REAL NOT NULL,
    first_month TEXT, last_month TEXT,
    PRIMARY KEY (project, title, agent)
);
CREATE TABLE IF NOT EXISTS project_views (
    project TEXT NOT NULL, agent TEXT NOT NULL, month TEXT NOT NULL,
    views INTEGER NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (project, agent, month)
);
CREATE TABLE IF NOT EXISTS meta (
    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    expires_at REAL NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

_TABLES = ("views", "not_found", "project_views", "meta")


class Cache:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=30.0, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        # Older caches used a title-wide 404. Keep them readable while new 404s
        # record the range that actually returned no data.
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(not_found)")}
        legacy = "first_month" not in columns or "last_month" not in columns
        for name in ("first_month", "last_month"):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE not_found ADD COLUMN {name} TEXT")
        if legacy:
            self._conn.execute("DELETE FROM not_found")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    @staticmethod
    def _is_provisional(month: str, window_end: str) -> bool:
        return months.diff(window_end, month) < T.PROVISIONAL_MONTHS

    # --- per-article views -------------------------------------------------

    def get_views(
        self, project: str, title: str, agent: str, wanted: Sequence[str]
    ) -> Tuple[Dict[str, int], List[str]]:
        """Return (fresh cached values, months that must be fetched)."""
        now = self._clock()
        with self._lock:
            not_found = self._conn.execute(
                "SELECT expires_at, first_month, last_month FROM not_found WHERE project=? AND title=? AND agent=?",
                (project, title, agent),
            ).fetchone()
            rows = self._conn.execute(
                "SELECT month, views FROM views WHERE project=? AND title=? AND agent=? "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (project, title, agent, now),
            ).fetchall()
        cached = dict(rows)
        if not_found and not_found[0] > now:
            first, last = not_found[1:]
            cached.update({m: 0 for m in wanted if m not in cached and
                           (first is None or first <= m) and (last is None or m <= last)})
        fresh = {m: cached[m] for m in wanted if m in cached}
        missing = [m for m in wanted if m not in cached]
        return fresh, missing

    def put_views(
        self,
        project: str,
        title: str,
        agent: str,
        values: Mapping[str, int],
        requested: Sequence[str],
        window_end: str,
    ) -> None:
        """Store a fetched range; a requested month absent from values is a zero."""
        now = self._clock()
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM not_found WHERE project=? AND title=? AND agent=?", (project, title, agent)
            )
            for month in requested:
                value = int(values.get(month, 0))
                provisional = self._is_provisional(month, window_end)
                if provisional and value == 0:
                    conn.execute(
                        "DELETE FROM views WHERE project=? AND title=? AND agent=? AND month=?",
                        (project, title, agent, month),
                    )
                    continue
                expires_at = now + T.PROVISIONAL_TTL_SECONDS if provisional else None
                conn.execute(
                    "INSERT OR REPLACE INTO views VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project, title, agent, month, value, expires_at, now),
                )

    def put_not_found(
        self, project: str, title: str, agent: str, requested: Optional[Sequence[str]] = None
    ) -> None:
        now = self._clock()
        first = requested[0] if requested else None
        last = requested[-1] if requested else None
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO not_found VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project, title, agent, now + T.NOT_FOUND_TTL_SECONDS, now, first, last),
            )

    def invalidate_views(self, project: str, stale_months: Sequence[str]) -> None:
        with self._tx() as conn:
            conn.executemany(
                "DELETE FROM views WHERE project=? AND month=?",
                [(project, month) for month in stale_months],
            )

    # --- project aggregate -------------------------------------------------

    def put_project_views(
        self, project: str, agent: str, values: Mapping[str, int], window_end: str
    ) -> List[str]:
        """Store the aggregate; return non-provisional months that changed beyond REVISION_TOLERANCE."""
        now = self._clock()
        revised: List[str] = []
        with self._tx() as conn:
            old = dict(
                conn.execute(
                    "SELECT month, views FROM project_views WHERE project=? AND agent=?",
                    (project, agent),
                ).fetchall()
            )
            for month in sorted(values):
                new = int(values[month])
                if month in old and not self._is_provisional(month, window_end):
                    if abs(new - old[month]) / max(old[month], 1) > T.REVISION_TOLERANCE:
                        revised.append(month)
                conn.execute(
                    "INSERT OR REPLACE INTO project_views VALUES (?, ?, ?, ?, ?)",
                    (project, agent, month, new, now),
                )
        return revised

    # --- metadata ----------------------------------------------------------

    def get_meta(self, kind: str, key: str) -> Optional[Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM meta WHERE kind=? AND key=?", (kind, key)
            ).fetchone()
        if row is None or row[1] <= self._clock():
            return None
        return json.loads(row[0])

    def put_meta(self, kind: str, key: str, value: Any, ttl: float = T.METADATA_TTL_SECONDS) -> None:
        now = self._clock()
        with self._tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta VALUES (?, ?, ?, ?, ?)",
                (kind, key, json.dumps(value, ensure_ascii=False), now + ttl, now),
            )

    # --- maintenance (cache info / cache clear) ----------------------------

    def info(self) -> Dict[str, Any]:
        with self._lock:
            rows = {
                table: self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in _TABLES
            }
        size = 0
        for suffix in ("", "-wal", "-shm"):
            part = Path(str(self.path) + suffix)
            if part.exists():
                size += part.stat().st_size
        return {"path": str(self.path), "size_bytes": size, "rows": rows}

    def clear(self, older_than_seconds: Optional[float] = None) -> int:
        deleted = 0
        with self._tx() as conn:
            for table in _TABLES:
                if older_than_seconds is None:
                    cursor = conn.execute(f"DELETE FROM {table}")
                else:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE fetched_at < ?",
                        (self._clock() - older_than_seconds,),
                    )
                deleted += cursor.rowcount
        return deleted
