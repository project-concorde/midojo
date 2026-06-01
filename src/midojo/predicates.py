"""Declarative predicate evaluator for suite grading.

Predicates are parsed from YAML and cover common grading patterns: substring
checks, field equality, list matching, and boolean combinators.

Verification providers can register additional predicate types at startup
via register_predicate_parser().
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from midojo.types import Environment

GradingContext = dict[str, Any] | None


def resolve_field(env: Environment, path: str) -> Any:
    """Walk a dotted path into a Pydantic model."""
    current: Any = env
    for key in path.split("."):
        if isinstance(current, dict):
            current = current[key]
        else:
            current = getattr(current, key)
    return current


# ---------------------------------------------------------------------------
# Predicate types
# ---------------------------------------------------------------------------


@dataclass
class OutputContains:
    value: str

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return self.value.lower() in agent_output.lower()


@dataclass
class OutputContainsAll:
    values: list[str]

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        lower = agent_output.lower()
        return all(v.lower() in lower for v in self.values)


@dataclass
class OutputContainsAny:
    values: list[str]

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        lower = agent_output.lower()
        return any(v.lower() in lower for v in self.values)


@dataclass
class EnvFieldEquals:
    field: str
    value: Any

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return resolve_field(post_env, self.field) == self.value


@dataclass
class EnvFieldContains:
    field: str
    value: str

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        field_val = resolve_field(post_env, self.field)
        return self.value.lower() in str(field_val).lower()


@dataclass
class EnvListAnyMatch:
    field: str
    match: dict[str, str]

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        items = resolve_field(post_env, self.field)
        if not isinstance(items, list):
            return False
        for item in items:
            if isinstance(item, dict):
                if all(k in item and self.match[k].lower() in str(item[k]).lower() for k in self.match):
                    return True
            else:
                if all(hasattr(item, k) and self.match[k].lower() in str(getattr(item, k)).lower() for k in self.match):
                    return True
        return False


@dataclass
class EnvListCount:
    field: str
    count: int

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        items = resolve_field(post_env, self.field)
        return isinstance(items, list) and len(items) == self.count


@dataclass
class EnvFieldUnchanged:
    field: str

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return resolve_field(pre_env, self.field) == resolve_field(post_env, self.field)


@dataclass
class AllOf:
    predicates: list[Predicate]

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return all(p.evaluate(agent_output, pre_env, post_env, ctx) for p in self.predicates)


@dataclass
class AnyOf:
    predicates: list[Predicate]

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return any(p.evaluate(agent_output, pre_env, post_env, ctx) for p in self.predicates)


@dataclass
class Not:
    predicate: Predicate

    def evaluate(
        self, agent_output: str, pre_env: Environment, post_env: Environment, ctx: GradingContext = None
    ) -> bool:
        return not self.predicate.evaluate(agent_output, pre_env, post_env, ctx)


Predicate = (
    OutputContains
    | OutputContainsAll
    | OutputContainsAny
    | EnvFieldEquals
    | EnvFieldContains
    | EnvListAnyMatch
    | EnvListCount
    | EnvFieldUnchanged
    | AllOf
    | AnyOf
    | Not
)

# ---------------------------------------------------------------------------
# Parser: raw YAML dict -> Predicate
# ---------------------------------------------------------------------------

_PARSERS: dict[str, Callable[[Any], Predicate]] = {
    "output_contains": lambda v: OutputContains(value=v),
    "output_contains_all": lambda v: OutputContainsAll(values=v),
    "output_contains_any": lambda v: OutputContainsAny(values=v),
    "env_field_equals": lambda v: EnvFieldEquals(field=v["field"], value=v["value"]),
    "env_field_contains": lambda v: EnvFieldContains(field=v["field"], value=v["value"]),
    "env_list_any_match": lambda v: EnvListAnyMatch(field=v["field"], match=v["match"]),
    "env_list_count": lambda v: EnvListCount(field=v["field"], count=v["count"]),
    "env_field_unchanged": lambda v: EnvFieldUnchanged(field=v["field"] if isinstance(v, dict) else v),
    "all_of": lambda v: AllOf(predicates=[parse_predicate(p) for p in v]),
    "any_of": lambda v: AnyOf(predicates=[parse_predicate(p) for p in v]),
    "not": lambda v: Not(predicate=parse_predicate(v)),
}


def register_predicate_parser(key: str, parser: Callable[[Any], Predicate]) -> None:
    """Register a predicate parser contributed by a verification provider."""
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
    ctx: GradingContext = None,
) -> bool:
    return predicate.evaluate(agent_output, pre_env, post_env, ctx)
