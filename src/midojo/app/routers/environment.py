from __future__ import annotations

import yaml
from agentdojo.task_suite.task_suite import InjectionVector, TaskSuite, read_suite_file
from fastapi import APIRouter, status

from midojo.app.dependencies import Suite
from midojo.app.models import InjectionVectorInfo

router = APIRouter(prefix="/environment")


def _get_injection_vectors(suite: TaskSuite) -> dict[str, InjectionVector]:
    text = read_suite_file(suite.name, "injection_vectors.yaml", suite.data_path)
    raw = yaml.safe_load(text)
    return {vid: InjectionVector.model_validate(vinfo) for vid, vinfo in raw.items()}


@router.get("", status_code=status.HTTP_200_OK)
def environment(suite: Suite) -> dict:
    env = suite.load_and_inject_default_environment({})
    return env.model_dump()


@router.get("/injection-vectors", status_code=status.HTTP_200_OK)
def injection_vectors(suite: Suite) -> dict[str, InjectionVectorInfo]:
    vectors = _get_injection_vectors(suite)
    return {vid: InjectionVectorInfo(description=v.description, default=v.default) for vid, v in vectors.items()}
