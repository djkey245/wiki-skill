"""One shape for what an agent did, whatever ran it (Claude Code, OpenRouter)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SKILL = "wikipedia-interest"


@dataclass
class ToolCall:
    name: str  # Bash, Read, Skill, Edit, Write, …
    command: str  # the Bash command, or the tool input as JSON
    output: str = ""
    is_error: bool = False
    path: Optional[str] = None  # file_path of Read / Edit / Write


@dataclass
class Turn:
    """One user message and everything the agent did until it answered."""
    user: str
    calls: List[ToolCall] = field(default_factory=list)
    answer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def commands(self) -> List[str]:
        return [c.command for c in self.calls if c.name == "Bash"]

    @property
    def skill_used(self) -> bool:
        for c in self.calls:
            if c.name == "Skill" and SKILL in c.command:
                return True
            if c.path and c.path.endswith(f"{SKILL}/SKILL.md"):
                return True
            if c.name == "Bash" and ("wikitrends.py" in c.command or "report.py" in c.command):
                return True
        return False


@dataclass
class Transcript:
    scenario: str
    runner: str
    model: str
    turns: List[Turn] = field(default_factory=list)
    status: str = "ok"  # ok | error | rate_limited
    session: Optional[str] = None
    replay_misses: int = 0

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def turn_from_claude_stream(lines: Iterable[str], user: str) -> "tuple[Turn, Optional[str]]":
    """Parse `claude -p --output-format stream-json --verbose` (Claude Code 2.1). Returns the
    turn and the session id for `--resume`. Unknown event types are ignored."""
    turn = Turn(user)
    pending: Dict[str, ToolCall] = {}
    session = None
    texts: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        session = event.get("session_id", session)
        kind = event.get("type")
        if kind == "assistant":
            for part in event.get("message", {}).get("content", []):
                if part.get("type") == "text" and part.get("text", "").strip():
                    texts.append(part["text"])
                elif part.get("type") == "tool_use":
                    data = part.get("input", {})
                    command = data.get("command") if part.get("name") == "Bash" else json.dumps(data, ensure_ascii=False, sort_keys=True)
                    call = ToolCall(part.get("name", ""), command or "", path=data.get("file_path"))
                    pending[part.get("id", "")] = call
                    turn.calls.append(call)
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for part in content if isinstance(content, list) else []:
                if part.get("type") == "tool_result" and part.get("tool_use_id") in pending:
                    call = pending[part["tool_use_id"]]
                    call.output = _text(part.get("content"))
                    call.is_error = bool(part.get("is_error"))
        elif kind == "result":
            usage = event.get("usage", {})
            turn.input_tokens = sum(int(usage.get(k, 0) or 0) for k in
                                    ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            turn.output_tokens = int(usage.get("output_tokens", 0) or 0)
            if event.get("is_error"):
                turn.error = str(event.get("result") or event.get("subtype"))
            if event.get("result"):
                texts.append(event["result"])
    turn.answer = texts[-1] if texts else ""
    return turn, session
