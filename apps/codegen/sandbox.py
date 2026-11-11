"""Run generated code and tests with pytest in a throwaway subprocess.

Demo sandbox, not a security boundary: a static import/name check, an isolated interpreter
(`python -I`), a temp directory, an empty environment without secrets, sockets disabled, resource
limits on POSIX and a wall-clock timeout. Determined code could still get around this.
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from apps.codegen.contract import CODE_MODULE, DECISION_SOURCE, TEST_MODULE

ALLOWED_IMPORTS = {
    "__future__", "dataclasses", "typing", "enum", "re", "math", "decimal", "fractions", "datetime",
    "functools", "itertools", "collections", "decision", CODE_MODULE, "pytest",
}
BLOCKED_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "input",
    "breakpoint", "getattr", "setattr", "delattr", "memoryview", "exit", "quit",
}

_NO_NETWORK = '''import socket


def _blocked(*args, **kwargs):
    raise OSError("Network access is disabled in the codegen sandbox")


socket.socket = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
'''
_RESULT_LINE = re.compile(r"::(\w+) (PASSED|FAILED|ERROR)")


@dataclass
class SandboxResult:
    status: str  # passed | failed | blocked | timeout
    output: str = ""
    blocked: list[str] = field(default_factory=list)
    per_test: dict[str, str] = field(default_factory=dict)
    passed: int = 0
    failed: int = 0
    duration_ms: int = 0


def check_source(source: str, filename: str) -> list[str]:
    """Problems that stop the code from running at all: disallowed imports, names and dunder access."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{filename}: does not parse ({exc.msg}, line {exc.lineno})"]
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""] if not node.level else ["relative import"]
        else:
            modules = []
        for module in modules:
            if module.split(".")[0] not in ALLOWED_IMPORTS:
                problems.append(f"{filename} line {node.lineno}: import of '{module}' is not allowed")
        if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            problems.append(f"{filename} line {node.lineno}: use of '{node.id}' is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            problems.append(f"{filename} line {node.lineno}: access to '{node.attr}' is not allowed")
    return problems


def _limit_resources() -> None:  # pragma: no cover - runs in the child process on POSIX only
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))


def run_pytest(code: str, tests: str, timeout: float = 30) -> SandboxResult:
    blocked = check_source(code, f"{CODE_MODULE}.py") + check_source(tests, f"{TEST_MODULE}.py")
    if blocked:
        return SandboxResult(
            status="blocked", blocked=blocked,
            output="Not run. The static check refused this code:\n" + "\n".join(blocked),
        )
    return run_unchecked(code, tests, timeout)


def run_unchecked(code: str, tests: str, timeout: float = 30) -> SandboxResult:
    """The subprocess part alone, without the static check."""
    with tempfile.TemporaryDirectory(prefix="codegen_") as tmp:
        folder = Path(tmp)
        (folder / "decision.py").write_text(DECISION_SOURCE, encoding="utf-8")
        (folder / f"{CODE_MODULE}.py").write_text(code, encoding="utf-8")
        (folder / f"{TEST_MODULE}.py").write_text(tests, encoding="utf-8")
        (folder / "conftest.py").write_text(_NO_NETWORK, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tmp,
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": tmp,
        }
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        command = [
            sys.executable, "-I", "-m", "pytest", "-v", "--no-header", "-p", "no:cacheprovider",
            "--rootdir", tmp, "--confcutdir", tmp, f"{TEST_MODULE}.py",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout,
                preexec_fn=_limit_resources if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            return SandboxResult(
                status="timeout", duration_ms=int((time.monotonic() - started) * 1000),
                output=f"Stopped after {timeout:g} seconds: the tests did not finish in time.\n{partial}",
            )
        duration = int((time.monotonic() - started) * 1000)
    output = (completed.stdout + ("\n" + completed.stderr if completed.stderr.strip() else "")).replace(tmp, "<sandbox>")
    per_test = {name: result for name, result in _RESULT_LINE.findall(output)}
    passed = sum(1 for result in per_test.values() if result == "PASSED")
    failed = len(per_test) - passed
    ok = completed.returncode == 0 and passed > 0
    return SandboxResult(
        status="passed" if ok else "failed", output=output.strip(), per_test=per_test,
        passed=passed, failed=failed if per_test else max(failed, 1), duration_ms=duration,
    )
