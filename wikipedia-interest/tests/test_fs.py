import json
import os

import pytest

from wikitrends import fs
from wikitrends.errors import WikitrendsError

needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)


@pytest.fixture
def dirs(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    return home, cwd


def read_only_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o500)
    return path


def test_explicit_workdir_is_created_and_absolute(dirs, tmp_path):
    home, cwd = dirs
    warnings = []
    result = fs.resolve_workdir(str(tmp_path / "wd"), {}, cwd, home, warnings)
    assert result == (tmp_path / "wd").resolve()
    assert result.is_dir()
    assert warnings == []


def test_env_home_used_when_no_flag(dirs, tmp_path):
    home, cwd = dirs
    result = fs.resolve_workdir(None, {"WIKITRENDS_HOME": str(tmp_path / "envwd")}, cwd, home, [])
    assert result == (tmp_path / "envwd").resolve()


def test_xdg_data_home(dirs, tmp_path):
    home, cwd = dirs
    result = fs.resolve_workdir(None, {"XDG_DATA_HOME": str(tmp_path / "xdg")}, cwd, home, [])
    assert result == (tmp_path / "xdg" / "wikipedia-interest").resolve()


def test_default_under_home_and_nothing_written_to_cwd(dirs):
    home, cwd = dirs
    result = fs.resolve_workdir(None, {}, cwd, home, [])
    assert result == (home / ".local" / "share" / "wikipedia-interest").resolve()
    assert list(cwd.iterdir()) == []


@needs_non_root
def test_fallback_to_cwd_when_default_not_writable(tmp_path):
    home = read_only_dir(tmp_path / "ro-home")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    warnings = []
    try:
        result = fs.resolve_workdir(None, {}, cwd, home, warnings)
    finally:
        home.chmod(0o700)
    assert result == (cwd / ".wikitrends").resolve()
    assert (result / ".gitignore").read_text() == "*\n"
    assert [w["code"] for w in warnings] == ["workdir_fallback"]
    assert warnings[0]["path"] == str(result)


@needs_non_root
def test_not_writable_anywhere(tmp_path):
    home = read_only_dir(tmp_path / "ro-home")
    cwd = read_only_dir(tmp_path / "ro-cwd")
    try:
        with pytest.raises(WikitrendsError) as info:
            fs.resolve_workdir(None, {}, cwd, home, [])
    finally:
        home.chmod(0o700)
        cwd.chmod(0o700)
    assert info.value.code == "workdir_not_writable"
    assert info.value.exit_code == 6
    assert "--workdir" in info.value.hint


@needs_non_root
def test_explicit_workdir_not_writable_has_no_fallback(dirs, tmp_path):
    home, cwd = dirs
    ro = read_only_dir(tmp_path / "ro")
    try:
        with pytest.raises(WikitrendsError) as info:
            fs.resolve_workdir(str(ro / "wd"), {}, cwd, home, [])
    finally:
        ro.chmod(0o700)
    assert info.value.code == "workdir_not_writable"
    assert list(cwd.iterdir()) == []


def test_gitignore_written_when_workdir_inside_git_repo(dirs, tmp_path):
    home, cwd = dirs
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = fs.resolve_workdir(str(repo / "analysis"), {}, cwd, home, [])
    assert (result / ".gitignore").read_text() == "*\n"


def test_no_gitignore_outside_git_repo(dirs, tmp_path):
    home, cwd = dirs
    result = fs.resolve_workdir(str(tmp_path / "plain"), {}, cwd, home, [])
    assert not (result / ".gitignore").exists()


def test_cache_dir_resolution(dirs, tmp_path):
    home, _ = dirs
    workdir = tmp_path / "wd"
    workdir.mkdir()
    assert fs.resolve_cache_dir({"WIKITRENDS_CACHE_DIR": str(tmp_path / "c")}, home, workdir) == (tmp_path / "c").resolve()
    assert fs.resolve_cache_dir({"XDG_CACHE_HOME": str(tmp_path / "xc")}, home, workdir) == (tmp_path / "xc" / "wikipedia-interest").resolve()
    assert fs.resolve_cache_dir({}, home, workdir) == (home / ".cache" / "wikipedia-interest").resolve()


@needs_non_root
def test_cache_dir_falls_back_to_workdir(tmp_path):
    home = read_only_dir(tmp_path / "ro-home")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    try:
        result = fs.resolve_cache_dir({}, home, workdir)
    finally:
        home.chmod(0o700)
    assert result == (workdir / ".cache").resolve()


def test_atomic_write_json_roundtrip_and_no_temp_files(tmp_path):
    target = tmp_path / "spec.json"
    fs.atomic_write_json(target, {"label": "астрономія", "n": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"label": "астрономія", "n": 1}
    assert "астрономія" in target.read_text(encoding="utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["spec.json"]


def test_interrupted_write_keeps_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "spec.json"
    fs.atomic_write_json(target, {"version": 1})

    def boom(src, dst):
        raise OSError("interrupted")

    monkeypatch.setattr(fs.os, "replace", boom)
    with pytest.raises(OSError):
        fs.atomic_write_json(target, {"version": 2})
    assert json.loads(target.read_text()) == {"version": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["spec.json"]
