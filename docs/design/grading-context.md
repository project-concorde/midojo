# Design note: GradingContext and the env / verification axes

Status: implemented
Branch: `acs-runtime-oracle`
Date: 2026-06-01

## Problem

Every predicate's `evaluate` used to repeat the same signature:

```python
def evaluate(self, agent_output, pre_env, post_env, ctx=None) -> bool
```

where `ctx` was a loose `dict[str, Any] | None`. It felt bolted on: it sat *next
to* the env args as a half-used optional 4th wheel, most predicates ignored it,
and the same long signature was repeated across ~13 predicate/combinator classes
(8 builtin, 2 ACS, 3 combinators).

The awkwardness was a symptom. The real issue: a predicate may read evidence from
two different places — the **environment** (pre/post snapshots, agent output) and a
**verifier** (RHACS runtime oracle, future filesystem/network/k8s backends) — and
those two sources were passed through different mechanisms (positional args vs.
the tacked-on `ctx` dict).

## Background: two orthogonal axes

Per the platform doc, MiDojo's observability extends along two axes that are
deliberately *not* a fixed pair (the relationship is many-to-many):

- **Environment backend** — *what the agent operates on*. YAML dict today;
  sandboxed container / cluster resource later. One env can be checked by several
  verifiers.
- **Verifier** — *how outcomes are checked*. Predicates over env state + output
  today; filesystem inspection, network traffic, k8s audit later. One verifier
  (e.g. the builtin one) works across many env backends.

Note: in the ACS case, the Rox client is **not** the environment — the live
pod/deployment is the environment; the Rox client is the *observability plane*
over it. The binding between them is `namespace / deployment / cluster + time
window`, resolved in `RhacsVerifier.setup()`.

## Decision

Collapse everything a predicate may read into a single typed `GradingContext`, and
give predicates one stable argument. (`GradingContext` is now this dataclass; the
old loose `dict | None` of the same intent is gone.)

```python
@dataclass
class GradingContext:
    agent_output: str
    pre_env: Environment
    post_env: Environment
    verifiers: dict[str, Verifier] = field(default_factory=dict)

    def verifier(self, name: str) -> Verifier:
        if name not in self.verifiers:
            raise RuntimeError(
                f"Predicate requires the '{name}' verifier, "
                f"which is not configured (check the relevant env vars)."
            )
        return self.verifiers[name]


class Predicate(Protocol):
    def evaluate(self, ctx: GradingContext) -> bool: ...
```

Predicates then read only what they need:

```python
class OutputContains:
    def evaluate(self, ctx: GradingContext) -> bool:
        return self.value.lower() in ctx.agent_output.lower()

class AcsProcessMatch:
    def evaluate(self, ctx: GradingContext) -> bool:
        verifier = ctx.verifier("rhacs")   # typed; raises its own clear error
        ...

class AllOf:
    def evaluate(self, ctx: GradingContext) -> bool:
        return all(p.evaluate(ctx) for p in self.predicates)
```

`evaluate_predicate(predicate, agent_output, pre_env, post_env, verifiers=None)`
stays as the ergonomic boundary: callers hold the loose values, it builds the
`GradingContext`, predicates consult the one object.

### Why a uniform signature (not split predicate kinds)

The tempting alternative — state predicates take `(output, pre, post)`, oracle
predicates take a verifier — breaks on the combinators. `all_of` / `any_of` mix
both kinds in one tree and must call a uniform `.evaluate()`. So the signature has
to be identical for everyone, which means one context object.

## What this buys

- One short signature everywhere; the ~13 repeated 4-arg litanies collapse.
- Predicates that don't need a verifier simply never call `ctx.verifier(...)` —
  env and output are right there on `ctx`. No optional tail param to ignore.
- `_require_provider` (free function) and the loose `dict | None` context type
  both disappear. The "missing verifier" error moves onto `ctx.verifier()`.
- Extending is free: add a field to `GradingContext` (e.g. `function_calls`, which
  `grade()` already receives but predicates can't see today) without touching any
  `evaluate` signature.

## The verifier dict

`state.verifiers` is a `dict[str, Verifier]` keyed by `.name`; `discover_verifiers()`
returns that dict. Dict preserves insertion order, so `setup()`/`settle()`
iteration is unaffected, and `runs.grade_evaluation` passes the dict straight into
the `GradingContext` rather than rebuilding a `{p.name: p}` map per grade.

Note: `predicates._PARSERS` is a separate, persistent registry keyed by predicate
YAML key (`"acs_process_match"` → parser fn), not by verifier name — a different
axis from `state.verifiers`.

## Naming

- The abstraction is `Verifier` (not `VerificationProvider`) — an agent-noun for
  "the thing that verifies outcomes". Module: `midojo/verifier.py`; implementations
  under `midojo/verifiers/`.
- The builtin one is `BuiltinVerifier` (predicates over env state + output), the
  RHACS one `RhacsVerifier`. A verifier is *not* an environment backend; the YAML
  dict loader in `YAMLTaskSuite` is the thing that would eventually become a
  "builtin env backend" — the *other* axis.
- The per-evaluation bundle is `GradingContext` (named for the operation it serves,
  and to avoid collision with the `Evaluation` state model).

## Explicitly deferred

Do **not** introduce an `EnvironmentBackend` ABC yet. There is exactly one env
backend (YAML dict) with no second implementation in sight; the abstraction would
be speculative. The verification axis earns its ABC because it already has two
real implementations (builtin + rhacs). Revisit when a container backend is
actually imminent.

## Files touched

- `predicates.py` — `GradingContext`, `Predicate` protocol, combinators,
  `evaluate_predicate`.
- `verifier.py` — the `Verifier` ABC (renamed from `verification.py`).
- `verifiers/builtin.py`, `verifiers/rhacs.py` — the two implementations and their
  predicates on the new signature; `_require_provider` dropped.
- `verifiers/__init__.py` — `discover_verifiers` / `bootstrap_verifiers` return a
  dict keyed by name.
- `yaml_task_suite.py` / `app/state.py` / `app/main.py` / `app/routers/runs.py` —
  thread the `verifiers` dict and build the `GradingContext` at grade time.
- Tests — `_ctx(verifier)` constructs a `GradingContext`.
