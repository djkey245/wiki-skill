import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wikitrends import cli

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wikitrends.py"


def run_cli(argv, tmp_path, env=None):
    stdout = io.StringIO()
    base_env = {
        "WIKITRENDS_HOME": str(tmp_path / "home-wd"),
        "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache"),
    }
    base_env.update(env or {})
    code = cli.run(argv, env=base_env, cwd=tmp_path, home=tmp_path, stdout=stdout)
    return code, json.loads(stdout.getvalue())


def test_cache_info(tmp_path):
    code, payload = run_cli(["cache", "info"], tmp_path)
    assert code == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == cli.SCHEMA_VERSION
    assert payload["path"] == str((tmp_path / "cache" / "cache.sqlite").resolve())
    assert payload["rows"] == {"views": 0, "not_found": 0, "project_views": 0, "meta": 0}
    assert "warnings" not in payload


def test_cache_clear_older_than(tmp_path):
    code, payload = run_cli(["cache", "clear", "--older-than", "90d"], tmp_path)
    assert code == 0
    assert payload["deleted"] == 0


def test_bad_duration_is_input_error(tmp_path):
    code, payload = run_cli(["cache", "clear", "--older-than", "soon"], tmp_path)
    assert code == 2
    assert payload["ok"] is False
    assert payload["code"] == "invalid_arguments"
    assert payload["schema_version"] == cli.SCHEMA_VERSION


def test_unknown_command_is_json_input_error(tmp_path):
    code, payload = run_cli(["frobnicate"], tmp_path)
    assert code == 2
    assert payload["code"] == "invalid_arguments"
    assert "hint" in payload


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)
def test_domain_error_maps_to_registry_exit_code(tmp_path):
    ro_parent = tmp_path / "ro"
    ro_parent.mkdir()
    ro_parent.chmod(0o500)
    try:
        code, payload = run_cli(["--workdir", str(ro_parent / "wd"), "cache", "info"], tmp_path)
    finally:
        ro_parent.chmod(0o700)
    assert code == 6
    assert payload["code"] == "workdir_not_writable"


def test_unexpected_exception_is_internal_error(tmp_path, monkeypatch, capsys):
    def boom(args, ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(cli.COMMANDS, ("cache", "info"), boom)
    code, payload = run_cli(["cache", "info"], tmp_path)
    assert code == 1
    assert payload["code"] == "internal_error"
    assert "kaboom" in payload["message"]
    assert "Traceback" in capsys.readouterr().err


def test_warnings_are_emitted(tmp_path, monkeypatch):
    def with_warning(args, ctx):
        ctx.warnings.append({"code": "workdir_fallback", "message": "x", "path": "/p"})
        return {}

    monkeypatch.setitem(cli.COMMANDS, ("cache", "info"), with_warning)
    _, payload = run_cli(["cache", "info"], tmp_path)
    assert payload["warnings"] == [{"code": "workdir_fallback", "message": "x", "path": "/p"}]


def test_entry_point_script_writes_single_json_object(tmp_path):
    env = {
        "WIKITRENDS_HOME": str(tmp_path / "wd"),
        "WIKITRENDS_CACHE_DIR": str(tmp_path / "cache"),
        "PATH": "/usr/bin:/bin",
    }
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "cache", "info"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert proc.stdout.count("\n") == 1
