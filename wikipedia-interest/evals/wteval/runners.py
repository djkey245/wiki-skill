"""Agent runners: Claude Code (`claude -p`) and OpenRouter (one bash tool). Both give Sessions
whose `send(text)` returns a Turn; a scenario's steps are sent to one session (testing §11)."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .transcript import SKILL, ToolCall, Turn, turn_from_claude_stream

# Variables of a surrounding Claude Code session: a nested `claude -p` would join that session.
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID")
SKIP_COPY = (".venv", "tests", "evals", "__pycache__", ".pytest_cache", "uv.lock", "pyproject.toml")


class RateLimited(Exception):
    """A provider's daily limit: the run stops and is reported as rate_limited, not failed."""


def clean_env(base: Mapping[str, str], extra: Mapping[str, str]) -> Dict[str, str]:
    env = {k: v for k, v in base.items() if k not in SESSION_VARS}
    env.update(extra)
    return env


def prepare_project(skill_dir: Path, root: Path) -> Tuple[Path, Path]:
    """An empty project with the skill in .claude/skills/ (reproducible attachment). Returns
    (project, installed skill dir)."""
    project = Path(root) / "project"
    target = project / ".claude" / "skills" / SKILL
    shutil.copytree(skill_dir, target, ignore=shutil.ignore_patterns(*SKIP_COPY))
    return project, target


# --- Claude Code ------------------------------------------------------------------------

ALLOWED_TOOLS = ("Bash", "Read", "Glob", "Grep", "Skill", "Edit", "Write")


class ClaudeSession:
    def __init__(self, runner: "ClaudeCodeRunner", project: Path, env: Dict[str, str]) -> None:
        self.runner, self.project, self.env = runner, project, env
        self.session: Optional[str] = None

    def send(self, text: str) -> Turn:
        r = self.runner
        cmd = [r.executable, "-p", text, "--model", r.model, "--output-format", "stream-json", "--verbose",
               "--max-turns", str(r.max_turns), "--allowedTools", *ALLOWED_TOOLS]
        if self.session:
            cmd += ["--resume", self.session]
        try:
            proc = subprocess.run(cmd, cwd=self.project, env=self.env, capture_output=True, text=True, timeout=r.timeout)
        except subprocess.TimeoutExpired:
            return Turn(text, error=f"timeout after {r.timeout} s")
        turn, session = turn_from_claude_stream(proc.stdout.splitlines(), text)
        self.session = session or self.session
        if proc.returncode != 0 and not turn.error:
            turn.error = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        return turn


class ClaudeCodeRunner:
    name = "claude"

    def __init__(self, model: str, executable: str = "claude", timeout: float = 900, max_turns: int = 60) -> None:
        self.model, self.executable, self.timeout, self.max_turns = model, executable, timeout, max_turns

    def session(self, project: Path, env: Dict[str, str], skill_dir: Path) -> ClaudeSession:
        return ClaudeSession(self, project, env)


# --- OpenRouter -----------------------------------------------------------------------------

API = "https://openrouter.ai/api/v1"
MIN_CONTEXT = 32000  # SKILL.md + system prompt + several CLI outputs of ~1.5k tokens
REQUESTS_PER_MINUTE = 20  # the :free limit
MAX_TOOL_ROUNDS = 40
OUTPUT_LIMIT = 20000
BASH_TOOL = {"type": "function", "function": {
    "name": "bash", "description": "Run a bash command in the project directory and return its stdout and stderr.",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}}
Http = Callable[[str, str, Dict[str, str], Optional[bytes]], Tuple[int, Dict[str, str], bytes]]


def urllib_http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Tuple[int, Dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def run_bash(command: str, project: Path, env: Dict[str, str]) -> Tuple[str, bool]:
    try:
        proc = subprocess.run(["bash", "-c", command], cwd=project, env=env, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "timeout after 300 s", True
    out = (proc.stdout + ("\n" + proc.stderr if proc.stderr.strip() else ""))[:OUTPUT_LIMIT]
    return out, proc.returncode != 0


def system_prompt(skill_dir: Path) -> str:
    text = (Path(skill_dir) / "SKILL.md").read_text(encoding="utf-8")
    return (f"You can use the skill below; <skill_dir> is {skill_dir}. Run commands with the bash tool. "
            f"Answer the user in their language.\n\n{text}")


class OpenRouterSession:
    def __init__(self, runner: "OpenRouterRunner", project: Path, env: Dict[str, str], skill_dir: Path) -> None:
        self.runner, self.project, self.env = runner, project, env
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt(skill_dir)}]

    def send(self, text: str) -> Turn:
        r = self.runner
        turn = Turn(text)
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_TOOL_ROUNDS):
            reply = r.complete(self.messages)
            usage = reply.get("usage") or {}
            turn.input_tokens += int(usage.get("prompt_tokens", 0) or 0)
            turn.output_tokens += int(usage.get("completion_tokens", 0) or 0)
            message = reply["choices"][0]["message"]
            self.messages.append({k: v for k, v in message.items() if k in ("role", "content", "tool_calls")})
            calls = message.get("tool_calls") or []
            if not calls:
                turn.answer = message.get("content") or ""
                return turn
            for call in calls:
                try:
                    command = json.loads(call["function"]["arguments"] or "{}").get("command", "")
                except ValueError:
                    command = ""
                output, failed = r.execute(command, self.project, self.env)
                turn.calls.append(ToolCall("Bash", command, output, failed))
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
        turn.error = f"more than {MAX_TOOL_ROUNDS} tool rounds"
        return turn


class OpenRouterRunner:
    name = "openrouter"

    def __init__(self, model: str, api_key: str, *, http: Http = urllib_http, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 execute: Callable[[str, Path, Dict[str, str]], Tuple[str, bool]] = run_bash) -> None:
        if not model.endswith(":free"):
            raise ValueError("use a concrete ':free' model, never the openrouter/free router (testing §11)")
        self.model, self.api_key, self.http, self.sleep, self.clock, self.execute = model, api_key, http, sleep, clock, execute
        self._starts: List[float] = []

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def check_model(self) -> Dict[str, Any]:
        """Tool calling and a context long enough, from the models API, before any run."""
        status, _, body = self.http("GET", f"{API}/models", self._headers(), None)
        if status != 200:
            raise ValueError(f"models API: HTTP {status}")
        model = next((m for m in json.loads(body)["data"] if m["id"] == self.model), None)
        if model is None:
            raise ValueError(f"{self.model} is not in the OpenRouter catalogue")
        if "tools" not in model.get("supported_parameters", []):
            raise ValueError(f"{self.model} does not support tool calling")
        if int(model.get("context_length") or 0) < MIN_CONTEXT:
            raise ValueError(f"{self.model}: context {model.get('context_length')} < {MIN_CONTEXT}")
        return model

    def _throttle(self) -> None:
        now = self.clock()
        self._starts = [t for t in self._starts if now - t < 60]
        if len(self._starts) >= REQUESTS_PER_MINUTE:
            self.sleep(60 - (now - self._starts[0]))
        self._starts.append(self.clock())

    def complete(self, messages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        body = json.dumps({"model": self.model, "messages": list(messages), "tools": [BASH_TOOL]}).encode("utf-8")
        for _ in range(4):
            self._throttle()
            status, headers, raw = self.http("POST", f"{API}/chat/completions", self._headers(), body)
            if status == 200:
                return json.loads(raw)
            if status == 429:
                wait = float({k.lower(): v for k, v in headers.items()}.get("retry-after", 10))
                if wait > 120 or b"per-day" in raw or b"per day" in raw:
                    raise RateLimited(raw.decode("utf-8", "replace")[:200])
                self.sleep(wait)
                continue
            raise RuntimeError(f"OpenRouter: HTTP {status}: {raw[:200]!r}")
        raise RateLimited("429 after retries")

    def session(self, project: Path, env: Dict[str, str], skill_dir: Path) -> OpenRouterSession:
        return OpenRouterSession(self, project, env, skill_dir)
