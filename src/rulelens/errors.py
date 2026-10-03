"""Exception types raised by RuleLens."""

from __future__ import annotations


class RuleLensError(Exception):
    """Base class for every error this library raises on purpose."""


class ExpressionError(RuleLensError):
    """A rule expression is malformed or ill-typed.

    Carries the source text and a character span so callers can point at the
    exact location of the problem.
    """

    def __init__(
        self, message: str, source: str, start: int, end: int | None = None, code: str = "E001"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code  # E001 syntax, E002 unknown name, E003 type error
        self.source = source
        self.start = start
        self.end = end if end is not None else start + 1

    def render(self) -> str:
        """Return the message followed by the source line and a caret underline."""
        width = max(1, min(self.end, len(self.source)) - self.start)
        pointer = " " * self.start + "^" * width
        return f"{self.message}\n    {self.source}\n    {pointer}"

    def __str__(self) -> str:
        return self.message


class PolicyError(RuleLensError):
    """A policy file is structurally invalid or failed validation."""

    def __init__(self, message: str, diagnostics: tuple[object, ...] = ()) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class DataError(RuleLensError):
    """Input data could not be read or does not match the policy schema."""
