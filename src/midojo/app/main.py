from __future__ import annotations

from fastapi import FastAPI

from midojo.yaml_task_suite import YAMLTaskSuite

from . import state
from .routers import current, runs, suite, tasks, tools


def create_app(suite_instance: YAMLTaskSuite) -> FastAPI:
    state.suite = suite_instance
    state.runs = {}
    state.current_run = None
    state.current_eval = None

    app = FastAPI()
    app.include_router(suite.router)
    app.include_router(tasks.router)
    app.include_router(tools.router)
    runs.register_environment_update_route(suite_instance.environment_type)
    app.include_router(runs.router)
    current.register_environment_update_route(suite_instance.environment_type)
    app.include_router(current.router)
    return app
