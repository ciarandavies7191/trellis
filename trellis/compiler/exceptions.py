"""Compiler-specific exceptions."""

from trellis.exceptions import TrellisError


class CompilerError(TrellisError):
    """
    Raised when the compiler cannot produce a valid pipeline after all repair attempts.

    Attributes:
        attempts:   Total LLM calls made (initial + repairs).
        last_yaml:  Raw LLM output from the final attempt.
        last_error: Validation error from the final attempt.
    """

    def __init__(self, message: str, *, attempts: int, last_yaml: str, last_error: str) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_yaml = last_yaml
        self.last_error = last_error
