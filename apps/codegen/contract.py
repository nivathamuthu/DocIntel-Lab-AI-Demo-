"""The fixed return type every generated function uses. Written by hand, not generated."""

OUTCOMES = ("required", "permitted", "not_permitted", "not_applicable", "conflict")
CODE_MODULE = "rules_code"
TEST_MODULE = "test_rules_code"

DECISION_SOURCE = '''"""The fixed return type for generated rule functions. Written by hand, not generated."""
from dataclasses import dataclass

OUTCOMES = ("required", "permitted", "not_permitted", "not_applicable", "conflict")


@dataclass(frozen=True)
class Decision:
    outcome: str
    rule_keys: tuple = ()
    reason: str = ""

    def __post_init__(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}; use one of {OUTCOMES}")
'''

CONTRACT_TEXT = """The function returns Decision(outcome, rule_keys, reason), imported with `from decision import Decision`.
outcome is exactly one of:
- "required": a rule says the action must be done (replace, inspect, record, ground ...).
- "permitted": a rule allows something (may be deferred, may stay fitted ...).
- "not_permitted": a rule forbids it (is not permitted, cannot be deferred ...).
- "not_applicable": no rule's condition is met by the inputs.
- "conflict": two rules listed as conflicting both apply to the inputs and disagree. rule_keys lists both rules. Never choose one side.
When a rule's exception applies, the exception decides instead of that rule's action.
rule_keys is a tuple of the keys of the rules that decided the outcome, for example ("R-0006",)."""
