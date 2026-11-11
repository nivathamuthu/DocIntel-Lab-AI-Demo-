"""Sandbox, test rendering, the repair loop, conflicts, mutation, eval and pages. Offline."""
import io

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from apps.codegen.models import CodegenRun
from apps.codegen.render import mutate_threshold, render_tests, threshold_changes, validate_cases
from apps.codegen.sandbox import check_source, run_pytest, run_unchecked
from apps.codegen.schemas import CheckPlan, CodeOutput, FailureVerdict
from apps.codegen.services import create_run, execute
from apps.ingest.models import Chunk, Document
from apps.rules.models import Rule, RuleConflict
from core import mock_llm
from core.models import EvalRun

pytestmark = pytest.mark.django_db

GOOD_CODE = '''from decision import Decision


def decide(flight_hours: float) -> Decision:
    if flight_hours >= 12000:
        return Decision("required", ("R-0001",), "limit reached")
    return Decision("not_applicable", (), "below the limit")
'''
GOOD_TESTS = '''from decision import Decision
from rules_code import decide


def test_at_limit():
    assert decide(12000).outcome == "required"


def test_below_limit():
    assert decide(11999).outcome == "not_applicable"
'''


def _rule(key: str, condition: str, action: str, exception: str = "", status: str = "approved") -> Rule:
    document = Document.objects.first()
    if document is None:
        document = Document(title="procedure", original_name="procedure.docx", sha256="a" * 64, size_bytes=1,
                            file_type="docx", status=Document.Status.DONE)
        document.file.save("procedure.docx", ContentFile(b"x"), save=False)
        document.save()
        Chunk.objects.create(document=document, ordinal=0, text="x", text_hash="h", token_count=1, metadata={})
    return Rule.objects.create(
        key=key, document=document, chunk=document.chunks.first(), page=2, condition=condition, action=action,
        exception=exception, source_quote=f"{condition}, {action}.", fingerprint=key, status=status,
        prompt_version="v2", llm_provider="mock",
    )


@pytest.fixture
def rules():
    limit = _rule("R-0001", "the flight hours on part 65-45210-3 reach 12000", "replace the side-stay")
    fleet = _rule("R-0002", "the part is on the narrow-body fleet", "replace it at 11500 flight hours")
    plan = _rule("R-0003", "flight hours reach 11000", "start planning the job", status="pending")
    RuleConflict.objects.create(rule_a=limit, rule_b=fleet, is_conflict=True, signature="s", kind="threshold",
                                severity="high", reason="12000 versus 11500 flight hours.", llm_provider="mock")
    return limit, fleet, plan


def test_sandbox_runs_passing_tests():
    result = run_pytest(GOOD_CODE, GOOD_TESTS)
    assert result.status == "passed" and result.passed == 2 and result.failed == 0
    assert result.per_test == {"test_at_limit": "PASSED", "test_below_limit": "PASSED"}


def test_sandbox_reports_failing_tests():
    result = run_pytest(GOOD_CODE.replace(">= 12000", "> 12000"), GOOD_TESTS)
    assert result.status == "failed" and result.per_test["test_at_limit"] == "FAILED"


@pytest.mark.parametrize("line", [
    "import os", "import subprocess", "from socket import socket", "import requests", "import shutil",
    "x = __import__('os')", "open('/etc/passwd')", "x = (1).__class__",
])
def test_sandbox_blocks_dangerous_code_before_running(line):
    result = run_pytest(GOOD_CODE + "\n" + line + "\n", GOOD_TESTS)
    assert result.status == "blocked" and result.blocked
    assert "Not run" in result.output


def test_static_check_allows_plain_helpers():
    assert check_source("import math\nfrom dataclasses import dataclass\nfrom decision import Decision\n", "x.py") == []


def test_sandbox_stops_code_that_runs_too_long():
    slow = GOOD_CODE.replace('    if flight_hours >= 12000:', '    while True:\n        pass\n    if flight_hours >= 12000:')
    result = run_pytest(slow, GOOD_TESTS, timeout=3)
    assert result.status == "timeout"


def test_sandbox_process_has_no_secrets_and_no_network(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "should-not-leak")
    probe = GOOD_TESTS + '''

def test_no_network():
    import socket
    try:
        socket.create_connection(("example.com", 80))
    except OSError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("network was reachable")


def test_no_secret():
    import os
    assert "GOOGLE_API_KEY" not in os.environ
'''
    assert run_pytest(GOOD_CODE, probe).status == "blocked"
    result = run_unchecked(GOOD_CODE, probe)
    assert result.per_test == {"test_at_limit": "PASSED", "test_below_limit": "PASSED",
                               "test_no_network": "PASSED", "test_no_secret": "PASSED"}


def test_code_output_must_define_the_named_function():
    with pytest.raises(ValueError, match="top-level function"):
        CodeOutput(function_name="decide", parameters=[{"name": "flight_hours", "type": "float"}],
                   code="def other(flight_hours):\n    return 1\n")
    with pytest.raises(ValueError, match="exactly the listed parameters"):
        CodeOutput(function_name="decide", parameters=[{"name": "hours", "type": "float"}],
                   code="def decide(flight_hours):\n    return 1\n")


def test_cases_are_coerced_and_bad_ones_dropped():
    plan = CheckPlan(cases=[
        {"name": "at_limit", "rule_key": "R-0001", "inputs": [{"name": "flight_hours", "value": "12,000"}],
         "expected_outcome": "required"},
        {"name": "wrong_name", "rule_key": "R-0001", "inputs": [{"name": "hours", "value": "1"}],
         "expected_outcome": "required"},
        {"name": "not_a_number", "rule_key": "R-0001", "inputs": [{"name": "flight_hours", "value": "lots"}],
         "expected_outcome": "required"},
        {"name": "conflict_without_any", "rule_key": "R-0001", "inputs": [{"name": "flight_hours", "value": "1"}],
         "expected_outcome": "conflict"},
    ])
    cases, problems = validate_cases(plan.cases, [{"name": "flight_hours", "type": "float"}], [])
    assert [case["inputs"]["flight_hours"] for case in cases] == [12000.0]
    assert len(problems) == 3


def test_rendered_tests_carry_rule_key_and_quote_and_check_conflict_keys():
    rules = [{"key": "R-0001", "quote": "Replace at 12000."}]
    cases = [{"name": "both", "rule_key": "R-0001", "inputs": {"flight_hours": 12000.0}, "expected": "conflict",
              "why": "Both apply.", "pair": ["R-0002", "R-0001"]}]
    text = render_tests("decide", [{"name": "flight_hours", "type": "float"}], cases, rules)
    assert "# R-0001" in text and 'quote: "Replace at 12000."' in text
    assert "set(['R-0001', 'R-0002']) <= set(decision.rule_keys)" in text
    compile(text, "test_rules_code.py", "exec")


def test_mutation_changes_a_rule_threshold():
    mutated, change = mutate_threshold(GOOD_CODE, [{"condition": "hours reach 12000", "action": "replace"}])
    assert "12001" in mutated and change == "changed 12000 to 12001"
    assert run_pytest(mutated, GOOD_TESTS).status == "failed"
    assert mutate_threshold(GOOD_CODE, [{"condition": "no number", "action": "x"}])[0] is None


def test_run_passes_and_surfaces_the_conflict(rules):
    run = execute(create_run(["R-0001"]))
    assert run.rule_keys == ["R-0001", "R-0002"] and run.added_keys == ["R-0002"]
    assert run.status == CodegenRun.Status.PASSED and run.attempts_used == 1
    assert run.conflict_surfaced is True
    assert 'Decision("conflict", ("R-0001", "R-0002")' in run.final_code
    assert run.mutation["ran"] and run.mutation["killed"]
    assert run.unreviewed is False


def test_pending_rules_are_marked_unreviewed(rules):
    run = execute(create_run(["R-0003"]))
    assert run.unreviewed and run.rules[0]["label"] == "unreviewed"
    assert run.status == CodegenRun.Status.PASSED


def test_failing_code_is_repaired(rules, monkeypatch):
    original = mock_llm._HANDLERS[CodeOutput]
    calls = []

    def broken_then_fixed(prompt):
        calls.append(prompt)
        output = original(prompt)
        if len(calls) == 1:
            output["code"] = output["code"].replace(">= 11000", "> 11000")
        return output

    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, broken_then_fixed)
    run = execute(create_run(["R-0003"]))
    attempts = list(run.attempts.all())
    assert [attempt.status for attempt in attempts] == ["failed", "passed"]
    assert attempts[0].verdicts[0]["fault"] == "code"
    assert "PYTEST OUTPUT:" in calls[1] and "FAILED" in calls[1]
    assert attempts[1].code != attempts[0].code and ">= 11000" in attempts[1].code
    assert run.status == CodegenRun.Status.PASSED and run.attempts_used == 2


def test_loop_stops_after_three_attempts(rules, monkeypatch, settings):
    settings.CODEGEN_MAX_ATTEMPTS = 3
    original = mock_llm._HANDLERS[CodeOutput]

    def always_broken(prompt):
        output = original(prompt)
        output["code"] = output["code"].replace(">= 11000", "> 11000")
        return output

    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, always_broken)
    run = execute(create_run(["R-0003"]))
    assert run.attempts.count() == 3 and run.attempts_used == 3
    assert run.status == CodegenRun.Status.NEEDS_REVIEW
    assert run.mutation["ran"] is False


def test_blocked_code_counts_as_an_attempt_and_is_repaired(rules, monkeypatch):
    original = mock_llm._HANDLERS[CodeOutput]
    calls = []

    def sneaky_then_clean(prompt):
        calls.append(prompt)
        output = original(prompt)
        if len(calls) == 1:
            output["code"] = "import os\n" + output["code"]
        return output

    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, sneaky_then_clean)
    run = execute(create_run(["R-0003"]))
    assert [attempt.status for attempt in run.attempts.all()] == ["blocked", "passed"]


def _with_bad_test(monkeypatch):
    original = mock_llm._HANDLERS[CheckPlan]

    def plan_with_bad_test(prompt):
        plan = original(prompt)
        plan["cases"].append({"name": "required_just_below_limit", "rule_key": "R-0004",
                              "inputs": [{"name": "flight_hours", "value": "11999"}],
                              "expected_outcome": "required", "why": "Misreads 'reach 12000'."})
        return plan

    monkeypatch.setitem(mock_llm._HANDLERS, CheckPlan, plan_with_bad_test)


def test_a_test_that_contradicts_the_rule_is_dropped_and_the_code_kept(monkeypatch):
    _rule("R-0004", "the flight hours reach 12000", "replace the side-stay")
    _with_bad_test(monkeypatch)
    code_calls = []
    original_code = mock_llm._HANDLERS[CodeOutput]
    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, lambda prompt: code_calls.append(prompt) or original_code(prompt))
    run = execute(create_run(["R-0004"]))
    first, second = run.attempts.all()
    assert first.status == "failed" and first.verdicts[0]["fault"] == "test"
    assert first.verdicts[0]["test"] == "test_required_just_below_limit"
    assert second.status == "passed" and second.code == first.code
    assert len(code_calls) == 1  # no repair call
    assert ">= 12000" in run.final_code and "test_required_just_below_limit" not in run.final_tests
    assert any("contradicts the rule text" in note for note in run.case_problems)
    assert run.status == CodegenRun.Status.PASSED


def test_an_ambiguous_rule_stops_the_run_for_review(rules, monkeypatch):
    original = mock_llm._HANDLERS[CodeOutput]

    def broken(prompt):
        output = original(prompt)
        output["code"] = output["code"].replace(">= 11000", "> 11000")
        return output

    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, broken)
    monkeypatch.setitem(mock_llm._HANDLERS, FailureVerdict,
                        lambda prompt: {"fault": "rule_ambiguous", "reason": "'reach 11000' may or may not include 11000."})
    run = execute(create_run(["R-0003"]))
    assert run.attempts.count() == 1
    assert run.status == CodegenRun.Status.NEEDS_REVIEW
    assert "ambiguous" in run.review_reason and "may or may not include" in run.review_reason
    assert "> 11000" in run.final_code


def test_a_repair_that_changes_a_rule_threshold_is_rejected(rules, monkeypatch):
    original = mock_llm._HANDLERS[CodeOutput]

    def broken_then_moves_threshold(prompt):
        output = original(prompt)
        if "PYTEST OUTPUT:" in prompt:
            output["code"] = output["code"].replace(">= 11000", ">= 10999")
        else:
            output["code"] = output["code"].replace(">= 11000", "> 11000")
        return output

    monkeypatch.setitem(mock_llm._HANDLERS, CodeOutput, broken_then_moves_threshold)
    run = execute(create_run(["R-0003"]))
    assert run.attempts.count() == 1
    assert run.status == CodegenRun.Status.NEEDS_REVIEW
    assert "removed or replaced rule threshold 11000" in run.review_reason
    assert "> 11000" in run.final_code


def test_threshold_guard_allows_operator_fixes_and_flags_moved_numbers():
    rules = [{"condition": "hours reach 12000", "action": "replace"}, {"condition": "fleet", "action": "at 11500"}]
    old = GOOD_CODE.replace(">= 12000", "> 12000")
    assert threshold_changes(old, GOOD_CODE, rules) == ""
    moved = GOOD_CODE.replace(">= 12000", ">= 11500")
    assert "12000" in threshold_changes(GOOD_CODE, moved, rules)
    assert "11999" in threshold_changes(GOOD_CODE, GOOD_CODE.replace("12000", "11999"), rules)


def test_eval_codegen_reports_rows(rules, tmp_path, monkeypatch):
    cases = tmp_path / "cases.yaml"
    cases.write_text(
        "- name: threshold\n  rules: ['flight hours reach 11000']\n"
        "- name: conflicting_pair\n  rules: ['reach 12000', 'narrow-body fleet']\n"
        "- name: missing\n  rules: ['no such rule text']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("apps.codegen.evaluate.DATASET", cases)
    out = io.StringIO()
    call_command("eval_codegen", stdout=out)
    eval_run = EvalRun.objects.get(kind="codegen")
    assert eval_run.metrics["runs"] == 2, eval_run.report_md
    assert eval_run.metrics["passed"] == 2, eval_run.report_md
    assert eval_run.metrics["conflicts_surfaced"] == 1 and eval_run.metrics["mutations_killed"] == 2
    assert "not that the rule" in eval_run.report_md
    assert "skipped" in out.getvalue()


def test_index_renders_empty(client):
    page = client.get(reverse("codegen:index")).content.decode()
    assert "No rules to turn into code." in page and "No runs yet." in page
    assert "not a security boundary" in page


def test_pages_render_with_data(client, rules):
    page = client.get(reverse("codegen:index")).content.decode()
    assert "R-0001" in page and "R-0003" not in page and "conflict with R-0002" in page
    unreviewed = client.get(reverse("codegen:index"), {"unreviewed": "1"}).content.decode()
    assert "R-0003" in unreviewed and "Unreviewed rules." in unreviewed and "Unreviewed rules." not in page
    assert "rules unreviewed (pending)" in page
    response = client.post(reverse("codegen:start"), {"rule": ["R-0001"]})
    run = CodegenRun.objects.get()
    assert response.status_code == 302 and response.url == reverse("codegen:run", args=[run.id])
    page = client.get(reverse("codegen:run", args=[run.id])).content.decode()
    assert "All tests passed" in page and "These rules disagree." in page and "Attempt 1" in page
    assert "Copy" in page
    download = client.get(reverse("codegen:download", args=[run.id, "rules_code.py"]))
    assert download.status_code == 200 and b"def decide" in download.content
    assert client.get(reverse("codegen:download", args=[run.id, "other.py"])).status_code == 404
    assert client.get(reverse("codegen:run_body", args=[run.id])).status_code == 200


def test_start_without_rules_explains_itself(client, rules):
    response = client.post(reverse("codegen:start"), follow=True)
    assert "Tick at least one rule" in response.content.decode()
