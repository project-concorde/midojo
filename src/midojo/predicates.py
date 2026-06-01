"""Predicate registry, combinators, and the per-evaluation grading context.

This module is the dispatch layer for suite grading predicates. Concrete
predicate types are contributed by verifiers at startup via
register_predicate_parser(). The combinators (AllOf, AnyOf, Not) live
here because they compose predicates rather than belonging to any verifier.

Every predicate reads from a single GradingContext, which carries the
two axes of grading evidence: the environment snapshots (pre/post) plus the
agent output, and handles to the verifiers bound to this
evaluation (RHACS runtime oracle, etc.).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from midojo.types import Environment

if TYPE_CHECKING:
    from midojo.verifier import Verifier


@dataclass
class GradingContext:
    """Everything a predicate may consult when grading one evaluation."""

    agent_output: str
    pre_env: Environment
    post_env: Environment
    verifiers: dict[str, Verifier] = field(default_factory=dict)

    def verifier(self, name: str) -> Verifier:
        """Look up a bound verifier, or raise a clear error."""
        if name not in self.verifiers:
            raise RuntimeError(
                f"Predicate requires the '{name}' verifier, "
                f"which is not configured (check the relevant env vars)."
            )
        return self.verifiers[name]


@runtime_checkable
class Predicate(Protocol):
    def evaluate(self, ctx: GradingContext) -> bool: ...


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


@dataclass
class AllOf:
    predicates: list[Predicate]

    def evaluate(self, ctx: GradingContext) -> bool:
        return all(p.evaluate(ctx) for p in self.predicates)


@dataclass
class AnyOf:
    predicates: list[Predicate]

    def evaluate(self, ctx: GradingContext) -> bool:
        return any(p.evaluate(ctx) for p in self.predicates)


@dataclass
class Not:
    predicate: Predicate

    def evaluate(self, ctx: GradingContext) -> bool:
        return not self.predicate.evaluate(ctx)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PARSERS: dict[str, Callable[[object], Predicate]] = {
    "all_of": lambda v: AllOf(predicates=[parse_predicate(p) for p in v]),
    "any_of": lambda v: AnyOf(predicates=[parse_predicate(p) for p in v]),
    "not": lambda v: Not(predicate=parse_predicate(v)),
}


def register_predicate_parser(key: str, parser: Callable[[object], Predicate]) -> None:
    """Register a predicate parser contributed by a verifier."""
    _PARSERS[key] = parser


def parse_predicate(raw: dict) -> Predicate:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError(f"Predicate must be a dict with exactly one key, got: {raw!r}")

    key = next(iter(raw))
    value = raw[key]

    if key not in _PARSERS:
        raise ValueError(f"Unknown predicate type: {key!r}. Must be one of: {sorted(_PARSERS)}")

    return _PARSERS[key](value)


def evaluate_predicate(
    predicate: Predicate,
    agent_output: str,
    pre_env: Environment,
    post_env: Environment,
    verifiers: dict[str, Verifier] | None = None,
) -> bool:
    """Build a GradingContext from loose grading inputs and evaluate.

    This is the ergonomic boundary between callers (which hold the raw
    output/env/verifier values) and predicates (which consult a single
    context object).
    """
    ctx = GradingContext(
        agent_output=agent_output,
        pre_env=pre_env,
        post_env=post_env,
        verifiers=verifiers or {},
    )
    return predicate.evaluate(ctx)
