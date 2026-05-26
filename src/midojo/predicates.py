"""Declarative predicate evaluator for suite grading.

Predicates are parsed from YAML and cover common grading patterns: substring
checks, field equality, list matching, and boolean combinators. Suites whose
grading logic fits these patterns need no Python code.

Limitation: there is no "run arbitrary code" predicate. If a suite needs
custom logic (e.g. regex matching, numeric ranges, fuzzy comparison), a new
predicate type must be added here. A future `custom_function` predicate could
allow suites to reference a Python callable for cases the built-in set can't
express.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentdojo.functions_runtime import TaskEnvironment


def resolve_field(env: TaskEnvironment, path: str) -> Any:
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

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return self.value.lower() in model_output.lower()


@dataclass
class OutputContainsAll:
    values: list[str]

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        lower = model_output.lower()
        return all(v.lower() in lower for v in self.values)


@dataclass
class OutputContainsAny:
    values: list[str]

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        lower = model_output.lower()
        return any(v.lower() in lower for v in self.values)


@dataclass
class EnvFieldEquals:
    field: str
    value: Any

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return resolve_field(post_env, self.field) == self.value


@dataclass
class EnvFieldContains:
    field: str
    value: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        field_val = resolve_field(post_env, self.field)
        return self.value.lower() in str(field_val).lower()


@dataclass
class EnvListAnyMatch:
    field: str
    match: dict[str, str]

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        items = resolve_field(post_env, self.field)
        if not isinstance(items, list):
            return False
        for item in items:
            if isinstance(item, dict):
                if all(k in item and self.match[k].lower() in str(item[k]).lower() for k in self.match):
                    return True
            else:
                if all(
                    hasattr(item, k) and self.match[k].lower() in str(getattr(item, k)).lower() for k in self.match
                ):
                    return True
        return False


@dataclass
class EnvListCount:
    field: str
    count: int

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        items = resolve_field(post_env, self.field)
        return isinstance(items, list) and len(items) == self.count


@dataclass
class EnvFieldUnchanged:
    field: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return resolve_field(pre_env, self.field) == resolve_field(post_env, self.field)


# ---------------------------------------------------------------------------
# Shell-agent predicates
# ---------------------------------------------------------------------------


@dataclass
class CommandsMatchPattern:
    """True if any executed shell command matches the regex pattern."""
    pattern: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        import re
        commands = getattr(post_env, "commands_executed", [])
        for cmd in commands:
            cmd_str = cmd.command if hasattr(cmd, "command") else str(cmd)
            if re.search(self.pattern, cmd_str):
                return True
        return False


@dataclass
class NetworkCallTo:
    """True if OpenShell OCSF logs record an ALLOWED connection to the given host.
    Populated in Phase 2 via GetSandboxLogs gRPC. Always False in Phase 1.
    """
    host: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        calls = getattr(post_env, "network_calls_allowed", [])
        return any(self.host.lower() in c.lower() for c in calls)


@dataclass
class NetworkCallBlockedTo:
    """True if OpenShell OCSF logs record a DENIED connection to the given host.
    Populated in Phase 2 via GetSandboxLogs gRPC. Always False in Phase 1.
    """
    host: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        calls = getattr(post_env, "network_calls_blocked", [])
        return any(self.host.lower() in c.lower() for c in calls)


@dataclass
class WorkspaceFileExists:
    """True if the given path was created (not seeded) during the agent session."""
    path: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        created = getattr(post_env, "files_created", [])
        return any(self.path in f for f in created)


@dataclass
class WorkspaceFileContains:
    """True if a file created during the session contains the given substring.
    Populated in Phase 2 via workspace_new_file_contents in ShellEnvironment.
    """
    path: str
    contains: str

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        contents = getattr(post_env, "workspace_new_file_contents", {})
        for file_path, content in contents.items():
            if self.path in file_path and self.contains.lower() in content.lower():
                return True
        return False


@dataclass
class AllOf:
    predicates: list[Predicate]

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return all(p.evaluate(model_output, pre_env, post_env) for p in self.predicates)


@dataclass
class AnyOf:
    predicates: list[Predicate]

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return any(p.evaluate(model_output, pre_env, post_env) for p in self.predicates)


@dataclass
class Not:
    predicate: Predicate

    def evaluate(self, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment) -> bool:
        return not self.predicate.evaluate(model_output, pre_env, post_env)


Predicate = (
    OutputContains
    | OutputContainsAll
    | OutputContainsAny
    | EnvFieldEquals
    | EnvFieldContains
    | EnvListAnyMatch
    | EnvListCount
    | EnvFieldUnchanged
    | CommandsMatchPattern
    | NetworkCallTo
    | NetworkCallBlockedTo
    | WorkspaceFileExists
    | WorkspaceFileContains
    | AllOf
    | AnyOf
    | Not
)

# ---------------------------------------------------------------------------
# Parser: raw YAML dict -> Predicate
# ---------------------------------------------------------------------------

_PARSERS: dict[str, type] = {
    "output_contains": OutputContains,
    "output_contains_all": OutputContainsAll,
    "output_contains_any": OutputContainsAny,
    "env_field_equals": EnvFieldEquals,
    "env_field_contains": EnvFieldContains,
    "env_list_any_match": EnvListAnyMatch,
    "env_list_count": EnvListCount,
    "env_field_unchanged": EnvFieldUnchanged,
    # Shell-agent predicates
    "commands_match_pattern": CommandsMatchPattern,
    "network_call_to": NetworkCallTo,
    "network_call_blocked_to": NetworkCallBlockedTo,
    "workspace_file_exists": WorkspaceFileExists,
    "workspace_file_contains": WorkspaceFileContains,
    # Combinators
    "all_of": AllOf,
    "any_of": AnyOf,
    "not": Not,
}


def parse_predicate(raw: dict) -> Predicate:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ValueError(f"Predicate must be a dict with exactly one key, got: {raw!r}")

    key = next(iter(raw))
    value = raw[key]

    if key not in _PARSERS:
        raise ValueError(f"Unknown predicate type: {key!r}. Must be one of: {sorted(_PARSERS)}")

    if key == "output_contains":
        return OutputContains(value=value)
    elif key == "output_contains_all":
        return OutputContainsAll(values=value)
    elif key == "output_contains_any":
        return OutputContainsAny(values=value)
    elif key == "env_field_equals":
        return EnvFieldEquals(field=value["field"], value=value["value"])
    elif key == "env_field_contains":
        return EnvFieldContains(field=value["field"], value=value["value"])
    elif key == "env_list_any_match":
        return EnvListAnyMatch(field=value["field"], match=value["match"])
    elif key == "env_list_count":
        return EnvListCount(field=value["field"], count=value["count"])
    elif key == "env_field_unchanged":
        return EnvFieldUnchanged(field=value["field"] if isinstance(value, dict) else value)
    elif key == "commands_match_pattern":
        return CommandsMatchPattern(pattern=value)
    elif key == "network_call_to":
        return NetworkCallTo(host=value)
    elif key == "network_call_blocked_to":
        return NetworkCallBlockedTo(host=value)
    elif key == "workspace_file_exists":
        return WorkspaceFileExists(path=value)
    elif key == "workspace_file_contains":
        return WorkspaceFileContains(path=value["path"], contains=value["contains"])
    elif key == "all_of":
        return AllOf(predicates=[parse_predicate(p) for p in value])
    elif key == "any_of":
        return AnyOf(predicates=[parse_predicate(p) for p in value])
    elif key == "not":
        return Not(predicate=parse_predicate(value))

    raise ValueError(f"Unhandled predicate type: {key!r}")


def evaluate_predicate(
    predicate: Predicate, model_output: str, pre_env: TaskEnvironment, post_env: TaskEnvironment
) -> bool:
    return predicate.evaluate(model_output, pre_env, post_env)
