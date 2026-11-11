"""generate_code -> generate_tests -> run_tests -> triage -> (repair | drop tests) -> run_tests, at most
CODEGEN_MAX_ATTEMPTS test runs.

The tests node is given the rule text, the conflicts and the function signature. It never receives the
code. When a test fails, triage asks a separate model call whether the code, the test or the rule text is
at fault. A wrong test is dropped and the code kept; an ambiguous rule stops the run for a person; only
code faults reach repair, and a repair that changes a rule threshold is rejected.
"""
from typing import TypedDict

from django.conf import settings
from langgraph.graph import END, START, StateGraph

from apps.codegen.contract import CONTRACT_TEXT
from apps.codegen.models import CodegenAttempt, CodegenRun
from apps.codegen.render import (
    conflicts_text, describe_case, failure_excerpt, render_tests, rules_text, signature_text, threshold_changes,
    validate_cases,
)
from apps.codegen.sandbox import run_pytest
from apps.codegen.schemas import CheckPlan, CodeOutput, FailureVerdict
from core.llm import structured_call
from core.prompts import render_prompt

PROMPT_VERSION = "v1"
REPAIR_PROMPT_VERSION = "v2"
MAX_OUTPUT_IN_PROMPT = 6000
MAX_TRIAGED_TESTS = 4


class CodegenError(RuntimeError):
    pass


class CodegenState(TypedDict, total=False):
    run_id: int
    rules: list[dict]
    conflicts: list[dict]
    function_name: str
    parameters: list[dict]
    code: str
    cases: list[dict]
    tests: str
    attempt: int
    max_attempts: int
    passed: bool
    last_output: str
    failing: list[str]
    code_faults: list[dict]
    stop_reason: str


def _shared(state: CodegenState) -> dict:
    return {"rules": rules_text(state["rules"]), "conflicts": conflicts_text(state["conflicts"]), "contract": CONTRACT_TEXT}


def _signature(state: CodegenState) -> str:
    return signature_text(state["function_name"], state["parameters"])


def generate_code(state: CodegenState) -> dict:
    prompt = render_prompt("codegen_code", PROMPT_VERSION, _shared(state), CodeOutput)
    output = structured_call(CodeOutput, prompt, task="codegen.generate_code",
                             prompt_name="codegen_code", prompt_version=PROMPT_VERSION)
    parameters = [param.model_dump() for param in output.parameters]
    CodegenRun.objects.filter(pk=state["run_id"]).update(function_name=output.function_name, parameters=parameters)
    return {"function_name": output.function_name, "parameters": parameters, "code": output.code}


def generate_tests(state: CodegenState) -> dict:
    prompt = render_prompt("codegen_tests", PROMPT_VERSION, {**_shared(state), "signature": _signature(state)}, CheckPlan)
    plan = structured_call(CheckPlan, prompt, task="codegen.generate_tests",
                           prompt_name="codegen_tests", prompt_version=PROMPT_VERSION)
    cases, problems = validate_cases(plan.cases, state["parameters"], state["conflicts"])
    CodegenRun.objects.filter(pk=state["run_id"]).update(cases=cases, case_problems=problems)
    if len(cases) < 2:
        detail = "; ".join(problems[:3]) or "no cases"
        raise CodegenError(f"The test writer produced fewer than two usable test cases ({detail}).")
    return {"cases": cases, "tests": render_tests(state["function_name"], state["parameters"], cases, state["rules"])}


def run_tests(state: CodegenState) -> dict:
    number = state.get("attempt", 0) + 1
    result = run_pytest(state["code"], state["tests"], timeout=settings.CODEGEN_TEST_TIMEOUT_SECONDS)
    CodegenAttempt.objects.create(
        run_id=state["run_id"], number=number, code=state["code"], tests=state["tests"], status=result.status,
        pytest_output=result.output, blocked=result.blocked, per_test=result.per_test,
        passed_count=result.passed, failed_count=result.failed, duration_ms=result.duration_ms,
    )
    CodegenRun.objects.filter(pk=state["run_id"]).update(attempts_used=number)
    failing = [name for name, outcome in result.per_test.items() if outcome != "PASSED"]
    return {"attempt": number, "passed": result.status == "passed", "last_output": result.output, "failing": failing}


def triage(state: CodegenState) -> dict:
    """Judge each failing test against the rule text: drop wrong tests, stop on ambiguity, pass code faults on."""
    by_name = {f"test_{case['name']}": case for case in state["cases"]}
    failing = [name for name in state.get("failing", []) if name in by_name]
    if not failing:
        # Blocked, timed out or not collected: nothing to judge per test, so the code is at fault.
        return {"code_faults": [{"test": "", "fault": "code", "reason": "The code did not run cleanly."}]}
    verdicts = []
    for name in failing[:MAX_TRIAGED_TESTS]:
        variables = {**_shared(state), "code": state["code"], "test": describe_case(by_name[name]),
                     "failure": failure_excerpt(state.get("last_output", ""), name)}
        prompt = render_prompt("codegen_triage", PROMPT_VERSION, variables, FailureVerdict)
        verdict = structured_call(FailureVerdict, prompt, task="codegen.triage",
                                  prompt_name="codegen_triage", prompt_version=PROMPT_VERSION)
        verdicts.append({"test": name, "fault": verdict.fault, "reason": verdict.reason})
    attempt = CodegenAttempt.objects.get(run_id=state["run_id"], number=state["attempt"])
    attempt.verdicts = verdicts
    attempt.save(update_fields=["verdicts"])

    ambiguous = [v for v in verdicts if v["fault"] == "rule_ambiguous"]
    if ambiguous:
        reasons = "; ".join(f"{v['test']}: {v['reason']}" for v in ambiguous)
        return {"stop_reason": f"The rule text is ambiguous, so the code was not changed. {reasons}"}

    update: dict = {"code_faults": [v for v in verdicts if v["fault"] == "code"]}
    wrong = {v["test"] for v in verdicts if v["fault"] == "test"}
    if wrong:
        run = CodegenRun.objects.get(pk=state["run_id"])
        notes = [f"{v['test']}: dropped after attempt {state['attempt']}, the test contradicts the rule text. "
                 f"{v['reason']}" for v in verdicts if v["fault"] == "test"]
        cases = [case for case in state["cases"] if f"test_{case['name']}" not in wrong]
        run.cases, run.case_problems = cases, run.case_problems + notes
        run.save(update_fields=["cases", "case_problems"])
        update["cases"] = cases
        update["tests"] = render_tests(state["function_name"], state["parameters"], cases, state["rules"])
        if len(cases) < 2:
            update["stop_reason"] = "Fewer than two tests remain after dropping tests that contradict the rule text."
    return update


def repair(state: CodegenState) -> dict:
    output_text = state.get("last_output", "")
    if len(output_text) > MAX_OUTPUT_IN_PROMPT:
        output_text = output_text[-MAX_OUTPUT_IN_PROMPT:]
    verdicts = "\n".join(f"- {v['test'] or 'whole run'}: {v['reason']}" for v in state.get("code_faults", []))
    variables = {**_shared(state), "signature": _signature(state), "code": state["code"],
                 "verdicts": verdicts or "- see the pytest output", "output": output_text}
    prompt = render_prompt("codegen_repair", REPAIR_PROMPT_VERSION, variables, CodeOutput)
    output = structured_call(CodeOutput, prompt, task="codegen.repair",
                             prompt_name="codegen_repair", prompt_version=REPAIR_PROMPT_VERSION)
    changed = threshold_changes(state["code"], output.code, state["rules"])
    if changed:
        return {"stop_reason": f"The repair was rejected because it {changed}. The code was kept as it was."}
    return {"code": output.code}


def after_tests(state: CodegenState) -> str:
    if state.get("passed") or state.get("attempt", 0) >= state.get("max_attempts", 3):
        return "end"
    return "triage"


def after_triage(state: CodegenState) -> str:
    if state.get("stop_reason"):
        return "end"
    return "repair" if state.get("code_faults") else "run_tests"


def after_repair(state: CodegenState) -> str:
    return "end" if state.get("stop_reason") else "run_tests"


def build_graph():
    graph = StateGraph(CodegenState)
    graph.add_node("generate_code", generate_code)
    graph.add_node("generate_tests", generate_tests)
    graph.add_node("run_tests", run_tests)
    graph.add_node("triage", triage)
    graph.add_node("repair", repair)
    graph.add_edge(START, "generate_code")
    graph.add_edge("generate_code", "generate_tests")
    graph.add_edge("generate_tests", "run_tests")
    graph.add_conditional_edges("run_tests", after_tests, {"end": END, "triage": "triage"})
    graph.add_conditional_edges("triage", after_triage, {"end": END, "repair": "repair", "run_tests": "run_tests"})
    graph.add_conditional_edges("repair", after_repair, {"end": END, "run_tests": "run_tests"})
    return graph.compile()
