"""What the code writer, the test writer and the repair step must return."""
import ast
from typing import Literal

from pydantic import BaseModel, Field, model_validator

IDENTIFIER = r"^[a-z_][a-z0-9_]{0,40}$"
Outcome = Literal["required", "permitted", "not_permitted", "not_applicable", "conflict"]


class Param(BaseModel):
    name: str = Field(pattern=IDENTIFIER)
    type: Literal["int", "float", "str", "bool"]
    description: str = Field(default="", max_length=200)


class CodeOutput(BaseModel):
    function_name: str = Field(pattern=IDENTIFIER)
    parameters: list[Param] = Field(min_length=1, max_length=6)
    code: str = Field(min_length=20, max_length=12000)

    @model_validator(mode="after")
    def _code_defines_the_function(self):
        try:
            tree = ast.parse(self.code)
        except SyntaxError as exc:
            raise ValueError(f"code does not parse: {exc.msg} (line {exc.lineno})") from exc
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == self.function_name]
        if not functions:
            raise ValueError(f"code must define a top-level function named {self.function_name}")
        names = [arg.arg for arg in functions[0].args.args]
        expected = [param.name for param in self.parameters]
        if names != expected:
            raise ValueError(f"the function's parameters {names} must be exactly the listed parameters {expected}")
        return self


class Arg(BaseModel):
    name: str = Field(pattern=IDENTIFIER)
    value: str = Field(max_length=200)


class RuleCheck(BaseModel):
    """One test case, written from the rule text only."""

    name: str = Field(pattern=r"^[a-z0-9_]{3,60}$")
    rule_key: str = Field(max_length=20)
    inputs: list[Arg] = Field(min_length=1, max_length=6)
    expected_outcome: Outcome
    why: str = Field(default="", max_length=240)


class CheckPlan(BaseModel):
    cases: list[RuleCheck] = Field(min_length=2, max_length=12)


class FailureVerdict(BaseModel):
    """Who is wrong when a test fails, judged against the rule text."""

    fault: Literal["code", "test", "rule_ambiguous"]
    reason: str = Field(min_length=3, max_length=400)
