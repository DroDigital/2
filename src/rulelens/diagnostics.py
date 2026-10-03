"""Structured findings produced while loading and linting a policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

ERROR: Final = "error"
WARNING: Final = "warning"
INFO: Final = "info"

_ORDER: Final = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One problem or observation about a policy.

    Attributes:
        severity: ``error`` (policy unusable), ``warning`` (likely a mistake) or ``info``.
        code: stable identifier such as ``E003`` or ``W102``; see ``docs/language.md``.
        message: human-readable description.
        rule_id: the rule concerned, if any.
        location: where in the policy file, e.g. ``rules[DTI_HIGH].when``.
        snippet: source excerpt with a caret for expression problems.
    """

    severity: str
    code: str
    message: str
    rule_id: str | None = None
    location: str | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in _fields(self).items() if v is not None}

    def sort_key(self) -> tuple[int, str, str]:
        return (_ORDER[self.severity], self.rule_id or "", self.code)


def _fields(d: Diagnostic) -> dict[str, Any]:
    return {
        "severity": d.severity,
        "code": d.code,
        "message": d.message,
        "rule_id": d.rule_id,
        "location": d.location,
        "snippet": d.snippet,
    }


def has_errors(diagnostics: list[Diagnostic] | tuple[Diagnostic, ...]) -> bool:
    return any(d.severity == ERROR for d in diagnostics)
