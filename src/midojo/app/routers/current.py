"""Routes that resolve the active run/eval dynamically.

SDKs running inside long-lived MCP servers can't be configured with run/eval
IDs upfront — those IDs don't exist when the server starts, and they change
across evaluations within a run. These routes let SDKs talk to the control
plane using only its base URL.

Resolution: ``state.current_run`` is set when a run is created;
``state.current_eval`` is set when an evaluation is created and cleared when
a new run starts. Only one active run + one active eval at a time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_current_eval
from ..models import (
    CreateFunctionCallRecord,
    FunctionCallRecord,
)
from ..state import Evaluation

router = APIRouter(prefix="/current")

_FC_ENV_FIELDS = {"pre_environment", "post_environment"}


@router.get("/environment", status_code=status.HTTP_200_OK)
def get_environment(evaluation: Annotated[Evaluation, Depends(get_current_eval)]) -> dict:
    return evaluation.environment.model_dump()


def register_environment_update_route(env_type: type) -> None:
    def update_environment(body, evaluation: Annotated[Evaluation, Depends(get_current_eval)]) -> dict:
        evaluation.environment = body
        return evaluation.environment.model_dump()

    update_environment.__annotations__["body"] = env_type
    router.add_api_route("/environment", update_environment, methods=["PUT"])


@router.get(
    "/function-calls",
    response_model=list[FunctionCallRecord],
    response_model_exclude={"__all__": _FC_ENV_FIELDS},
    status_code=status.HTTP_200_OK,
)
def list_function_calls(evaluation: Annotated[Evaluation, Depends(get_current_eval)]) -> list[FunctionCallRecord]:
    return evaluation.function_calls


@router.get(
    "/function-calls/{idx}",
    response_model=FunctionCallRecord,
    status_code=status.HTTP_200_OK,
)
def get_function_call(idx: int, evaluation: Annotated[Evaluation, Depends(get_current_eval)]) -> FunctionCallRecord:
    if idx < 0 or idx >= len(evaluation.function_calls):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Function call index out of range: {idx}")
    return evaluation.function_calls[idx]


@router.post(
    "/function-calls",
    response_model=FunctionCallRecord,
    status_code=status.HTTP_201_CREATED,
)
def record_function_call(
    req: CreateFunctionCallRecord,
    evaluation: Annotated[Evaluation, Depends(get_current_eval)],
) -> FunctionCallRecord:
    if evaluation.function_calls:
        pre_env = evaluation.function_calls[-1].post_environment
    else:
        pre_env = evaluation.pre_environment
    record = FunctionCallRecord(
        **req.model_dump(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        pre_environment=pre_env,
        post_environment=evaluation.environment.model_copy(deep=True),
    )
    evaluation.function_calls.append(record)
    return record
