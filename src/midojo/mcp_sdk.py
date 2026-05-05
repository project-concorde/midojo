"""MCP SDK — Python equivalent of pi-sdk.

Lets suite authors write standalone fake MCP servers whose tools talk to the
midojo control plane for environment access and function-call recording.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

import httpx
from fastmcp import FastMCP


class ToolContext:
    """Async access to the evaluation environment on the control plane."""

    def __init__(self, client: ControlPlaneClient) -> None:
        self._client = client

    async def env(self, field: str) -> Any:
        environment = await self._client.get_environment()
        return environment[field]

    async def env_update(self, field: str, value: Any) -> None:
        environment = await self._client.get_environment()
        environment[field] = value
        await self._client.put_environment(environment)


class ControlPlaneClient:
    def __init__(
        self,
        base_url: str,
        run_id: str,
        eval_id: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        base = base_url.rstrip("/")
        self._base_url = f"{base}/runs/{run_id}/evaluations/{eval_id}"
        self._http = http or httpx.AsyncClient()
        self._env_cache: dict[str, Any] | None = None

    async def get_environment(self) -> dict[str, Any]:
        if self._env_cache is not None:
            return self._env_cache
        resp = await self._http.get(f"{self._base_url}/environment")
        resp.raise_for_status()
        self._env_cache = resp.json()
        return self._env_cache

    async def put_environment(self, env: dict[str, Any]) -> None:
        self._env_cache = env
        resp = await self._http.put(
            f"{self._base_url}/environment",
            json=env,
        )
        resp.raise_for_status()

    async def record_function_call(
        self,
        *,
        function: str,
        args: dict,
        result: str,
        error: str | None = None,
    ) -> None:
        try:
            await self._http.post(
                f"{self._base_url}/function-calls",
                json={
                    "function": function,
                    "args": args,
                    "result": result,
                    "error": error,
                },
            )
        except httpx.HTTPError:
            pass

    def create_tool_context(self) -> ToolContext:
        return ToolContext(self)


class MidojoMCP:
    """Wrapper around FastMCP that adds control plane wiring.

    Usage::

        mcp = MidojoMCP("weather", control_plane_url=..., run_id=..., eval_id=...)

        @mcp.tool()
        async def get_weather(ctx: ToolContext, city: str) -> str:
            cities = await ctx.env("cities")
            ...

    The ``ctx: ToolContext`` first parameter is injected by the SDK and
    stripped from the MCP tool schema exposed to agents.
    """

    def __init__(
        self,
        name: str,
        *,
        control_plane_url: str,
        run_id: str,
        eval_id: str,
    ) -> None:
        self._fastmcp = FastMCP(name)
        self._client = ControlPlaneClient(control_plane_url, run_id, eval_id)

    def tool(self):
        def decorator(fn):
            sig = inspect.signature(fn, eval_str=True)
            params = list(sig.parameters.values())
            if not params or params[0].annotation is not ToolContext:
                raise TypeError(
                    f"First parameter of {fn.__name__} must be annotated as ToolContext"
                )
            user_params = params[1:]
            user_sig = sig.replace(parameters=user_params)

            @functools.wraps(fn)
            async def wrapper(**kwargs):
                ctx = self._client.create_tool_context()
                result: str = ""
                error: str | None = None
                try:
                    result = await fn(ctx, **kwargs)
                except Exception as e:
                    error = str(e)
                    result = error
                    raise
                finally:
                    await self._client.record_function_call(
                        function=fn.__name__,
                        args=kwargs,
                        result=result,
                        error=error,
                    )
                return result

            wrapper.__signature__ = user_sig
            wrapper.__annotations__ = {
                p.name: p.annotation
                for p in user_params
                if p.annotation is not inspect.Parameter.empty
            }

            self._fastmcp.tool(wrapper, name=fn.__name__, description=fn.__doc__)
            return fn

        return decorator

    def http_app(self, path: str = "/") -> Any:
        return self._fastmcp.http_app(path=path)

    def run(self, **kwargs) -> None:
        self._fastmcp.run(**kwargs)
