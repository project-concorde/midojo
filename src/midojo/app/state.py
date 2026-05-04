"""Process-local application state.

Persistence is in-memory only (lost on restart). A db store may replace
this layer later.
"""

from __future__ import annotations

from midojo.yaml_task_suite import YAMLTaskSuite

from .models import Evaluation, Run

suite: YAMLTaskSuite = None  # type: ignore[assignment]
runs: dict[str, Run] = {}
current_eval: Evaluation | None = None
