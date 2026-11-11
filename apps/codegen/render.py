"""Turn rules into prompt text, test cases into a pytest file, and thresholds into a mutation check."""
import ast
import re

from apps.codegen.contract import CODE_MODULE
from apps.rules.services.text import thresholds

PY_TYPES = {"int": int, "float": float, "str": str, "bool": bool}


def rules_text(rules: list[dict]) -> str:
    blocks = []
    for rule in rules:
        lines = [f"{rule['key']} ({rule['label']}) · {rule['citation']}",
                 f"  condition: {rule['condition']}",
                 f"  action: {rule['action']}"]
        if rule.get("exception"):
            lines.append(f"  exception: {rule['exception']}")
        lines.append(f'  quote: "{rule["quote"]}"')
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def conflicts_text(conflicts: list[dict]) -> str:
    if not conflicts:
        return "None."
    return "\n".join(f"{c['a']} vs {c['b']}: {c['reason']}" for c in conflicts)


def signature_text(function_name: str, parameters: list[dict]) -> str:
    args = ", ".join(f"{p['name']}: {p['type']}" for p in parameters)
    lines = [f"def {function_name}({args}) -> Decision"]
    lines += [f"  {p['name']}: {p['description']}" for p in parameters if p.get("description")]
    return "\n".join(lines)


def coerce(value: str, kind: str):
    text = value.strip()
    if kind == "str":
        return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" else text
    if kind == "bool":
        lowered = text.lower()
        if lowered in ("true", "yes", "1"):
            return True
        if lowered in ("false", "no", "0"):
            return False
        raise ValueError(f"'{value}' is not true or false")
    number = float(text.replace(",", "").replace("_", ""))
    if kind == "int":
        if not number.is_integer():
            raise ValueError(f"'{value}' is not a whole number")
        return int(number)
    return number


def validate_cases(cases: list, parameters: list[dict], conflicts: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep the cases that give every parameter a value of the right type; explain the others."""
    kinds = {p["name"]: p["type"] for p in parameters}
    kept, problems, used = [], [], set()
    for case in cases:
        given = {arg.name: arg.value for arg in case.inputs}
        missing = [name for name in kinds if name not in given]
        unknown = [name for name in given if name not in kinds]
        if missing or unknown:
            problems.append(f"{case.name}: dropped, inputs {sorted(given)} do not match the parameters {list(kinds)}")
            continue
        try:
            values = {name: coerce(given[name], kinds[name]) for name in kinds}
        except ValueError as exc:
            problems.append(f"{case.name}: dropped, {exc}")
            continue
        pair = []
        if case.expected_outcome == "conflict":
            pair = next(([c["a"], c["b"]] for c in conflicts if case.rule_key in (c["a"], c["b"])), [])
            if not pair and conflicts:
                pair = [conflicts[0]["a"], conflicts[0]["b"]]
            if not pair:
                problems.append(f"{case.name}: dropped, it expects a conflict but the selected rules have none")
                continue
        name = case.name
        while name in used:
            name += "_2"
        used.add(name)
        kept.append({
            "name": name, "rule_key": case.rule_key, "inputs": values,
            "expected": case.expected_outcome, "why": case.why, "pair": pair,
        })
    return kept, problems


def _comment(text: str, width: int = 96) -> list[str]:
    words, lines, line = " ".join(text.split()).split(" "), [], "#"
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = "#  "
        line += " " + word
    lines.append(line)
    return lines


def render_tests(function_name: str, parameters: list[dict], cases: list[dict], rules: list[dict]) -> str:
    by_key = {rule["key"]: rule for rule in rules}
    lines = [
        '"""Tests written from the rule text only. The test writer never saw the code."""',
        "from decision import Decision",
        f"from {CODE_MODULE} import {function_name}",
        "",
    ]
    for case in cases:
        rule = by_key.get(case["rule_key"])
        lines += ["", ""]
        if rule:
            lines.append(f"# {rule['key']}")
            lines += _comment(f'quote: "{rule["quote"]}"')
        if case["why"]:
            lines += _comment(f"why: {case['why']}")
        args = ", ".join(f"{p['name']}={case['inputs'][p['name']]!r}" for p in parameters)
        expected = case["expected"]
        lines += [
            f"def test_{case['name']}():",
            f"    decision = {function_name}({args})",
            '    assert isinstance(decision, Decision), "the function must return a Decision"',
            f"    assert decision.outcome == {expected!r}, "
            f"f\"expected {expected!r}, got {{decision.outcome!r}}: {{decision.reason}}\"",
        ]
        if expected == "conflict":
            pair = sorted(case["pair"])
            lines.append(
                f"    assert set({pair!r}) <= set(decision.rule_keys), "
                f"f\"a conflict must name both rules {pair}, got {{decision.rule_keys}}\""
            )
    return "\n".join(lines) + "\n"


def rule_numbers(rules: list[dict]) -> set[float]:
    found = set()
    for rule in rules:
        for number in thresholds(" ".join([rule["condition"], rule["action"], rule.get("exception", "")])):
            found.add(float(number))
    return found


def mutate_threshold(code: str, rules: list[dict]) -> tuple[str | None, str]:
    """Raise the largest rule threshold that appears as a literal in the code by one."""
    numbers = rule_numbers(rules)
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None, "the code does not parse"
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and float(node.value) in numbers
    ]
    if not candidates:
        return None, "no rule threshold appears as a number in the code"
    target = max(candidates, key=lambda node: node.value)
    original = target.value
    target.value = original + 1
    for node in candidates:
        if node is not target and node.value == original:
            node.value = original + 1
    return ast.unparse(tree), f"changed {original:g} to {original + 1:g}"


def _numbers(code: str, compares_only: bool = False) -> list[float]:
    tree = ast.parse(code)
    roots = [node for node in ast.walk(tree) if isinstance(node, ast.Compare)] if compares_only else [tree]
    found = []
    for root in roots:
        for node in ast.walk(root):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                found.append(float(node.value))
    return found


def threshold_changes(old_code: str, new_code: str, rules: list[dict]) -> str:
    """'' when the new code keeps every rule threshold of the old code; otherwise what changed."""
    numbers = rule_numbers(rules)
    try:
        old_all, new_all = _numbers(old_code), _numbers(new_code)
        old_compared, new_compared = set(_numbers(old_code, True)), set(_numbers(new_code, True))
    except SyntaxError:
        return ""
    lost = [n for n in sorted(numbers) if new_all.count(n) < old_all.count(n)]
    added = sorted(n for n in new_compared - old_compared if n not in numbers)
    problems = []
    if lost:
        problems.append("removed or replaced rule threshold " + ", ".join(f"{n:g}" for n in lost))
    if added:
        problems.append("compares against new number " + ", ".join(f"{n:g}" for n in added) + " that is not in the rule text")
    return "; ".join(problems)


def describe_case(case: dict) -> str:
    inputs = ", ".join(f"{name}={value!r}" for name, value in case["inputs"].items())
    lines = [f"test_{case['name']} (about {case['rule_key']})", f"inputs: {inputs}",
             f"expected outcome: {case['expected']}"]
    if case.get("why"):
        lines.append(f"test writer's reason: {case['why']}")
    return "\n".join(lines)


def failure_excerpt(output: str, test_name: str, limit: int = 1500) -> str:
    """The pytest failure section for one test, or the end of the output."""
    marker = f"_ {test_name} _"
    start = output.find(marker)
    if start < 0:
        return output[-limit:]
    end = output.find("\n____", start + len(marker))
    end = output.find("short test summary", start) if end < 0 else end
    return output[start:end if end > 0 else None][:limit]


def failing_lines(output: str) -> set[int]:
    return {
        index for index, line in enumerate(output.splitlines())
        if line.startswith("E ") or re.search(r"\bFAILED\b|\bERROR\b|Error\b", line)
    }
