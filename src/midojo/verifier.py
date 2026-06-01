"""Verifier ABC and registry.

Verifiers extend MiDojo's grading system with external
backends (RHACS runtime oracle, filesystem inspection, k8s audit, etc.).
Each verifier contributes predicate types for suite YAML and handles
its own lifecycle (setup, settle, teardown).
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from midojo.predicates import Predicate


class Verifier(abc.ABC):
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Verifier identifier used as the key in grading context."""

    @classmethod
    @abc.abstractmethod
    def predicate_parsers(cls) -> dict[str, Callable[[Any], Predicate]]:
        """YAML predicate key -> parser function.

        Class-level so parsers can be registered even when the backend
        is not configured. Parsing succeeds; evaluation fails with a
        clear error if the verifier is missing at grading time.
        """

    @abc.abstractmethod
    def setup(self, created_at: str, completed_at: str) -> None:
        """Prepare for grading an evaluation.

        Resolve external IDs, set the time window, etc. Called once
        per grade_evaluation() invocation, before settle().
        """

    def settle(self) -> None:
        """Wait for event propagation. Default no-op."""

    @classmethod
    @abc.abstractmethod
    def from_env(cls) -> Verifier | None:
        """Create from environment variables. Returns None if not configured."""
