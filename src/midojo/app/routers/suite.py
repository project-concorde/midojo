from __future__ import annotations

from typing import Annotated

from agentdojo.task_suite.task_suite import TaskSuite
from fastapi import APIRouter, Depends, status

from midojo.yaml_task_suite import YAMLTaskSuite

from ..dependencies import get_suite
from ..models import (
    CheckResponse,
    InjectionTaskCheckResult,
    InjectionVectorInfo,
    SuiteInfoResponse,
    UserTaskCheckResult,
)

router = APIRouter(prefix="/suite")


def _get_injection_vector_info(suite: TaskSuite) -> dict[str, InjectionVectorInfo]:
    if isinstance(suite, YAMLTaskSuite):
        raw = suite.get_injection_vectors_raw()
        return {vid: InjectionVectorInfo(description=v["description"], default=v["default"]) for vid, v in raw.items()}
    raise TypeError("Suite must be a YAMLTaskSuite")


@router.get("", response_model=SuiteInfoResponse, status_code=status.HTTP_200_OK)
def suite_info(suite: Annotated[TaskSuite, Depends(get_suite)]):
    return SuiteInfoResponse(
        user_tasks=list(suite.user_tasks.keys()),
        injection_tasks=list(suite.injection_tasks.keys()),
        tools=[t.name for t in suite.tools],
        injection_vectors=_get_injection_vector_info(suite),
    )


@router.get("/check", response_model=CheckResponse, status_code=status.HTTP_200_OK)
def check(suite: Annotated[TaskSuite, Depends(get_suite)]):
    passed, (user_results, injection_results) = suite.check(check_injectable=False)
    return CheckResponse(
        passed=passed,
        user_tasks={tid: UserTaskCheckResult(passed=ok, message=msg) for tid, (ok, msg) in user_results.items()},
        injection_tasks={tid: InjectionTaskCheckResult(passed=ok) for tid, ok in injection_results.items()},
    )
