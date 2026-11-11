"""Offline stand-ins for the codegen prompts (LLM_PROVIDER=mock). They exist for the test suite.

The code writer builds `decide(flight_hours)` from the numbers in each rule; the test writer
builds boundary cases from the same numbers, without reading the code. None of this parses
code written by a model.
"""
import re

from apps.codegen.schemas import CheckPlan, CodeOutput, FailureVerdict
from apps.rules.services.text import thresholds
from core.mock_llm import register

_RULE_HEAD = re.compile(r"^(R-\d{4}) \(", re.MULTILINE)
_CONFLICT = re.compile(r"^(R-\d{4}) vs (R-\d{4}):", re.MULTILINE)


def _section(prompt: str, start: str, end: str) -> str:
    begin = prompt.find(start)
    if begin < 0:
        return ""
    finish = prompt.find(end, begin)
    return prompt[begin + len(start):finish if finish >= 0 else None]


def _rules(prompt: str) -> list[dict]:
    block = _section(prompt, "RULES:", "CONFLICTS BETWEEN THESE RULES:")
    rules = []
    for chunk in re.split(r"\n\s*\n", block):
        head = _RULE_HEAD.search(chunk)
        if not head:
            continue
        fields = dict(re.findall(r"^\s+(condition|action|exception|quote): (.*)$", chunk, re.MULTILINE))
        numbers = thresholds(f"{fields.get('condition', '')} {fields.get('action', '')}")
        rules.append({
            "key": head.group(1),
            "action": fields.get("action", ""),
            "quote": fields.get("quote", "").strip('"'),
            "threshold": max((float(n) for n in numbers), default=None),
        })
    return rules


def _conflicts(prompt: str) -> list[tuple[str, str]]:
    return _CONFLICT.findall(_section(prompt, "CONFLICTS BETWEEN THESE RULES:", "DECISION CONTRACT:"))


def _outcome(action: str) -> str:
    text = action.lower()
    if "not permitted" in text or "cannot" in text or "must not" in text:
        return "not_permitted"
    if re.search(r"\bmay\b", text):
        return "permitted"
    return "required"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


@register(CodeOutput)
def mock_code(prompt: str) -> dict:
    rules = {rule["key"]: rule for rule in _rules(prompt) if rule["threshold"] is not None}
    lines = ["from decision import Decision", "", "", "def decide(flight_hours: float) -> Decision:"]
    in_conflict = set()
    for a, b in _conflicts(prompt):
        if a in rules and b in rules:
            in_conflict |= {a, b}
            low = min(rules[a]["threshold"], rules[b]["threshold"])
            lines += [
                f"    # {a} and {b} disagree; both apply from {_number(low)} flight hours.",
                f"    if flight_hours >= {_number(low)}:",
                f'        return Decision("conflict", ("{a}", "{b}"), "{a} and {b} disagree")',
            ]
    ordered = sorted((r for k, r in rules.items() if k not in in_conflict), key=lambda r: -r["threshold"])
    for rule in ordered:
        lines += [
            f'    # {rule["key"]}: "{rule["quote"][:80]}"',
            f"    if flight_hours >= {_number(rule['threshold'])}:",
            f'        return Decision("{_outcome(rule["action"])}", ("{rule["key"]}",), "threshold reached")',
        ]
    lines.append('    return Decision("not_applicable", (), "no rule applies")')
    return {
        "function_name": "decide",
        "parameters": [{"name": "flight_hours", "type": "float", "description": "Flight hours accumulated."}],
        "code": "\n".join(lines) + "\n",
    }


@register(FailureVerdict)
def mock_verdict(prompt: str) -> dict:
    """A test that expects a rule's action below that rule's own threshold is the test's fault."""
    block = _section(prompt, "FAILING TEST:", "PYTEST FAILURE:")
    key = re.search(r"\(about (R-\d{4})\)", block)
    hours = re.search(r"flight_hours=([\d.]+)", block)
    expected = re.search(r"expected outcome: (\w+)", block)
    rule = next((r for r in _rules(prompt) if key and r["key"] == key.group(1)), None)
    if (rule and rule["threshold"] is not None and hours and expected
            and expected.group(1) not in ("not_applicable", "conflict") and float(hours.group(1)) < rule["threshold"]):
        return {"fault": "test", "reason": f"The test expects the action below {_number(rule['threshold'])}, "
                                           "which the rule text does not say."}
    return {"fault": "code", "reason": "The expected outcome follows from the rule text."}


@register(CheckPlan)
def mock_tests(prompt: str) -> dict:
    rules = {rule["key"]: rule for rule in _rules(prompt) if rule["threshold"] is not None}
    cases, in_conflict = [], set()
    for a, b in _conflicts(prompt):
        if a in rules and b in rules:
            in_conflict |= {a, b}
            for value in sorted({rules[a]["threshold"], rules[b]["threshold"]}):
                cases.append({"name": f"conflict_{a}_{b}_at_{_number(value)}".lower().replace("-", "_"),
                              "rule_key": a, "inputs": [{"name": "flight_hours", "value": _number(value)}],
                              "expected_outcome": "conflict", "why": f"{a} and {b} both apply."})
    for key, rule in rules.items():
        if key in in_conflict:
            continue
        cases.append({"name": f"at_threshold_{key}".lower().replace("-", "_"), "rule_key": key,
                      "inputs": [{"name": "flight_hours", "value": _number(rule["threshold"])}],
                      "expected_outcome": _outcome(rule["action"]), "why": "The threshold is reached."})
    lowest = min((rule["threshold"] for rule in rules.values()), default=1.0)
    cases.append({"name": "nothing_applies", "rule_key": next(iter(rules), "none"),
                  "inputs": [{"name": "flight_hours", "value": _number(max(lowest - 1, 0))}],
                  "expected_outcome": "not_applicable", "why": "Below every threshold."})
    if len(cases) < 2:
        cases.append({"name": "nothing_applies_at_zero", "rule_key": "none",
                      "inputs": [{"name": "flight_hours", "value": "0"}],
                      "expected_outcome": "not_applicable", "why": "No flight hours."})
    return {"cases": cases[:12]}
