from __future__ import annotations

from typing import Annotated

from agentdojo.task_suite.task_suite import TaskSuite
from fastapi import APIRouter, Depends, status

from midojo.yaml_task_suite import YAMLTaskSuite

from ..dependencies import get_current_eval, get_suite
from ..models import Evaluation, InjectionVectorInfo

router = APIRouter(prefix="/environment")


def register_update_route(env_type: type) -> None:
    def update_environment(body, evaluation: Annotated[Evaluation, Depends(get_current_eval)]) -> dict:
        evaluation.environment = body
        return evaluation.environment.model_dump()

    update_environment.__annotations__["body"] = env_type
    router.add_api_route("", update_environment, methods=["PUT"])


@router.get("", status_code=status.HTTP_200_OK)
def environment(suite: Annotated[TaskSuite, Depends(get_suite)]) -> dict:
    env = suite.load_and_inject_default_environment({})
    return env.model_dump()


@router.get("/injection-vectors", status_code=status.HTTP_200_OK)
def injection_vectors(suite: Annotated[TaskSuite, Depends(get_suite)]) -> dict[str, InjectionVectorInfo]:
    if not isinstance(suite, YAMLTaskSuite):
        raise TypeError("Suite must be a YAMLTaskSuite")
    raw = suite.get_injection_vectors_raw()
    return {vid: InjectionVectorInfo(description=v["description"], default=v["default"]) for vid, v in raw.items()}
