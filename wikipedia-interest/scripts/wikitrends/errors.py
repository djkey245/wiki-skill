"""Registry of CLI codes (architecture §3) and the typed error raised by core modules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CodeInfo:
    ok: bool
    exit_code: int
    status: Optional[str] = None


REGISTRY: Dict[str, CodeInfo] = {
    # input errors
    "invalid_arguments": CodeInfo(False, 2),
    "langs_required": CodeInfo(False, 2),
    "display_lang_required": CodeInfo(False, 2),
    "unknown_lang": CodeInfo(False, 2),
    "period_too_short": CodeInfo(False, 2),
    "end_not_available": CodeInfo(False, 2),
    "topic_required": CodeInfo(False, 2),
    "limits_exceeded": CodeInfo(False, 2),
    "budget_exceeded": CodeInfo(False, 2),
    # regular stops that need a user decision
    "ambiguous_topic": CodeInfo(True, 0, "needs_user"),
    "existing_spec": CodeInfo(True, 0, "needs_user"),
    # data
    "no_data": CodeInfo(False, 3),
    # network
    "network_blocked": CodeInfo(False, 4),
    "network_unavailable": CodeInfo(False, 4),
    "tls_certificates_missing": CodeInfo(False, 4),
    "network_error": CodeInfo(False, 4),
    # environment
    "report_deps_missing": CodeInfo(False, 5),
    "workdir_not_writable": CodeInfo(False, 6),
    # internal
    "internal_error": CodeInfo(False, 1),
}

WARNING_CODES = frozenset(
    {"workdir_fallback", "start_clipped", "data_revised", "fetch_failed", "newer_data_available"}
)


class WikitrendsError(Exception):
    """An error with a registry code; the CLI turns it into JSON and an exit code."""

    def __init__(self, code: str, message: str, hint: Optional[str] = None, **details: Any) -> None:
        info = REGISTRY.get(code)
        if info is None or info.ok:
            raise ValueError(f"{code!r} is not an error code in the registry")
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    @property
    def exit_code(self) -> int:
        return REGISTRY[self.code].exit_code

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"ok": False, "code": self.code, "message": self.message}
        if self.hint is not None:
            payload["hint"] = self.hint
        payload.update(self.details)
        return payload


def needs_user(code: str, message: str, **details: Any) -> Dict[str, Any]:
    """Payload for a regular stop that requires the user's decision (ok: true, exit 0)."""
    info = REGISTRY.get(code)
    if info is None or info.status != "needs_user":
        raise ValueError(f"{code!r} is not a needs_user code")
    payload: Dict[str, Any] = {"ok": True, "status": "needs_user", "code": code, "message": message}
    payload.update(details)
    return payload


def make_warning(code: str, message: str, **details: Any) -> Dict[str, Any]:
    if code not in WARNING_CODES:
        raise ValueError(f"{code!r} is not a warning code")
    warning: Dict[str, Any] = {"code": code, "message": message}
    warning.update(details)
    return warning
