import pytest

from wikitrends import errors
from wikitrends.errors import REGISTRY, WikitrendsError, make_warning, needs_user

EXPECTED_EXIT = {
    "invalid_arguments": 2, "langs_required": 2, "display_lang_required": 2, "unknown_lang": 2,
    "period_too_short": 2, "end_not_available": 2, "topic_required": 2, "limits_exceeded": 2,
    "budget_exceeded": 2, "ambiguous_topic": 0, "existing_spec": 0, "no_data": 3,
    "network_blocked": 4, "network_unavailable": 4, "tls_certificates_missing": 4,
    "network_error": 4, "report_deps_missing": 5, "workdir_not_writable": 6, "internal_error": 1,
}


def test_registry_matches_spec_table():
    assert {code: info.exit_code for code, info in REGISTRY.items()} == EXPECTED_EXIT


def test_needs_user_codes_are_ok_true():
    assert REGISTRY["ambiguous_topic"].ok is True
    assert REGISTRY["ambiguous_topic"].status == "needs_user"
    assert REGISTRY["existing_spec"].status == "needs_user"


@pytest.mark.parametrize("code", sorted(c for c, i in REGISTRY.items() if not i.ok))
def test_every_error_code_builds_payload(code):
    err = WikitrendsError(code, "msg", hint="do this", extra=1)
    payload = err.to_payload()
    assert payload == {"ok": False, "code": code, "message": "msg", "hint": "do this", "extra": 1}
    assert err.exit_code == EXPECTED_EXIT[code]


def test_error_rejects_unknown_and_needs_user_codes():
    with pytest.raises(ValueError):
        WikitrendsError("no_such_code", "x")
    with pytest.raises(ValueError):
        WikitrendsError("ambiguous_topic", "x")


def test_needs_user_payload():
    payload = needs_user("ambiguous_topic", "Which one?", options=[{"qid": "Q1"}])
    assert payload == {
        "ok": True, "status": "needs_user", "code": "ambiguous_topic",
        "message": "Which one?", "options": [{"qid": "Q1"}],
    }
    with pytest.raises(ValueError):
        needs_user("network_error", "x")


def test_make_warning_validates_code():
    assert make_warning("workdir_fallback", "used ./.wikitrends", path="/x") == {
        "code": "workdir_fallback", "message": "used ./.wikitrends", "path": "/x",
    }
    with pytest.raises(ValueError):
        make_warning("network_error", "not a warning")


def test_warning_codes():
    assert errors.WARNING_CODES == frozenset(
        {"workdir_fallback", "start_clipped", "data_revised", "fetch_failed", "newer_data_available"}
    )
