"""Choice of <workdir> and cache directory with sandbox fallback; atomic file writes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, List, Mapping, Optional

from .errors import WikitrendsError, make_warning

APP_DIR = "wikipedia-interest"
FALLBACK_DIR = ".wikitrends"


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory and os.replace, so readers never see half a file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _ensure_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=str(path), prefix=".probe-")
        os.close(fd)
        os.unlink(probe)
        return True
    except OSError:
        return False


def _in_git_repo(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in [path, *path.parents])


def _write_gitignore(path: Path) -> None:
    gitignore = path / ".gitignore"
    if not gitignore.exists():
        atomic_write_text(gitignore, "*\n")


def _not_writable(path: Path) -> WikitrendsError:
    return WikitrendsError(
        "workdir_not_writable",
        f"Cannot write to {path}",
        hint="Repeat the command with --workdir <a directory you can write to>",
        path=str(path),
    )


def resolve_workdir(
    cli_value: Optional[str],
    env: Mapping[str, str],
    cwd: Path,
    home: Path,
    warnings: List[dict],
) -> Path:
    explicit = cli_value or env.get("WIKITRENDS_HOME")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not _ensure_writable(path):
            raise _not_writable(path)
        if _in_git_repo(path):
            _write_gitignore(path)
        return path

    data_home = env.get("XDG_DATA_HOME")
    default = (Path(data_home) if data_home else Path(home) / ".local" / "share") / APP_DIR
    default = default.expanduser().resolve()
    if _ensure_writable(default):
        if _in_git_repo(default):
            _write_gitignore(default)
        return default

    fallback = (Path(cwd) / FALLBACK_DIR).resolve()
    if not _ensure_writable(fallback):
        raise _not_writable(fallback)
    _write_gitignore(fallback)
    warnings.append(
        make_warning(
            "workdir_fallback",
            f"{default} is not writable; using {fallback}",
            path=str(fallback),
        )
    )
    return fallback


def resolve_cache_dir(env: Mapping[str, str], home: Path, workdir: Path) -> Path:
    explicit = env.get("WIKITRENDS_CACHE_DIR")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not _ensure_writable(path):
            raise _not_writable(path)
        return path

    cache_home = env.get("XDG_CACHE_HOME")
    default = (Path(cache_home) if cache_home else Path(home) / ".cache") / APP_DIR
    default = default.expanduser().resolve()
    if _ensure_writable(default):
        return default

    fallback = (Path(workdir) / ".cache").resolve()
    if not _ensure_writable(fallback):
        raise _not_writable(fallback)
    return fallback
