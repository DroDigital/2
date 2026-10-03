"""RuleLens: explainable business-rule decisions with static analysis and impact diffing."""

from .decision import Decision, PolicyRef, RuleResult
from .diagnostics import Diagnostic
from .errors import DataError, ExpressionError, PolicyError, RuleLensError
from .policy import Policy, PolicyTest, Rule
from .trace import Clause

__version__ = "0.1.0"

__all__ = [
    "Clause",
    "DataError",
    "Decision",
    "Diagnostic",
    "ExpressionError",
    "Policy",
    "PolicyError",
    "PolicyRef",
    "PolicyTest",
    "Rule",
    "RuleLensError",
    "RuleResult",
    "__version__",
]
