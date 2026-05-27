from __future__ import annotations

import abc
import asyncio
import os
import uuid

import httpx


class AgentClient(abc.ABC):
    @abc.abstractmethod
    async def send_task(self, prompt: str) -> str:
        """Send a task prompt to the agent and return its final text output."""
        ...


class SimpleHTTPAgentClient(AgentClient):
    """Sends a prompt via HTTP POST and returns the response text.

    Expects the agent to accept ``{"prompt": "..."}`` and return
    ``{"response": "..."}``.
    """

    def __init__(self, agent_url: str, timeout: float = 120.0) -> None:
        self.agent_url = agent_url
        self.timeout = timeout

    async def send_task(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.agent_url, json={"prompt": prompt})
            resp.raise_for_status()
            data = resp.json()
            for key in ("response", "output", "text"):
                if key in data:
                    return data[key]
            raise ValueError(
                f"Agent response JSON has no recognized key (expected 'response', 'output', or 'text'). "
                f"Got keys: {list(data.keys())}. Is the agent using the expected protocol? "
                f"(Use --protocol a2a for A2A agents.)"
            )


class A2AAgentClient(AgentClient):
    """Sends a task via the A2A protocol and returns the agent's text output."""

    def __init__(self, agent_url: str, timeout: float = 120.0) -> None:
        self.agent_url = agent_url
        self.timeout = timeout

    async def send_task(self, prompt: str) -> str:
        from a2a.client import ClientConfig, create_client
        from a2a.types import Message, Part, Role, SendMessageRequest

        config = ClientConfig(
            httpx_client=httpx.AsyncClient(timeout=self.timeout),
        )
        client = await create_client(self.agent_url, client_config=config)
        try:
            request = SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id=uuid.uuid4().hex,
                    parts=[Part(text=prompt)],
                ),
            )

            result_text = ""
            async for event in client.send_message(request):
                payload_type = event.WhichOneof("payload")
                if payload_type == "message":
                    for part in event.message.parts:
                        if part.text:
                            result_text += part.text
                elif payload_type == "task":
                    for artifact in event.task.artifacts:
                        for part in artifact.parts:
                            if part.text:
                                result_text += part.text
                elif payload_type == "artifact_update":
                    for part in event.artifact_update.artifact.parts:
                        if part.text:
                            result_text = part.text

            return result_text
        finally:
            await client.close()


class OGXResponsesClient(AgentClient):
    """Calls the OGX (Llama Stack) Responses API directly.

    The Responses API runs the tool loop server-side — the model discovers
    and calls MCP tools without client involvement. This is the vector
    tested by ogx-ai/ogx#5036: server-side tool results bypass guardrails.
    """

    def __init__(
        self,
        ogx_url: str,
        model: str,
        mcp_server_url: str,
        *,
        instructions: str = "",
        shield_id: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.ogx_url = ogx_url
        self.model = model
        self.mcp_server_url = mcp_server_url
        self.instructions = instructions
        self.shield_id = shield_id
        self.timeout = timeout

    async def send_task(self, prompt: str) -> str:
        from ogx_client import OgxClient

        client = OgxClient(base_url=self.ogx_url, timeout=self.timeout)
        kwargs: dict = dict(
            model=self.model,
            input=prompt,
            instructions=self.instructions,
            tools=[
                {
                    "type": "mcp",
                    "server_label": "weather",
                    "server_url": self.mcp_server_url,
                    "require_approval": "never",
                },
            ],
            stream=False,
        )
        if self.shield_id:
            kwargs["guardrails"] = [self.shield_id]

        response = client.responses.create(**kwargs)
        return response.output_text


class ShellOGXAgentClient(AgentClient):
    """Red-team agent client for shell-executing agents via OGX Responses API + OpenShell.

    Lifecycle per evaluation:
      1. Read injected workspace_file_contents from control plane (/current/environment)
      2. Snapshot current OpenShell sandboxes (for sandbox ID correlation)
      3. Create container via OGX Containers API
      4. Seed /workspace files from injected contents
      5. Mark workspace baseline (timestamp file) for diff
      6. Record evaluation start time (for OCSF log window)
      7. Call OGX Responses API with container_reference
      8. Extract shell_call items → record as function calls to control plane
      9. Workspace diff → read contents of newly created files
      10. Fetch OCSF events from OpenShell (if openshell_endpoint provided)
      11. Update ShellEnvironment on control plane
      12. Delete container

    Sandbox ID correlation (Phase 2):
      OGX's Containers API doesn't expose the underlying OpenShell sandbox ID.
      We diff SandboxClient.list() before/after container creation to find the new
      sandbox and get its ID for GetSandboxLogs.
      HACK: breaks under concurrent evaluations. Proper fix: contribute sandbox_id
      to OGX fork's Container model response (_row_to_container in containers.py).
    """

    def __init__(
        self,
        ogx_url: str,
        control_url: str,
        *,
        openshell_endpoint: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.ogx_url = ogx_url.rstrip("/")
        self.control_url = control_url.rstrip("/")
        self.openshell_endpoint = openshell_endpoint
        self.timeout = timeout

    async def send_task(self, prompt: str) -> str:
        import asyncio
        import time

        from openai import AsyncOpenAI

        from midojo.openshell_logs import fetch_ocsf_events, list_sandbox_refs
        from midojo.shell_environment import CommandRecord, ShellEnvironment

        http = httpx.AsyncClient(base_url=self.ogx_url, timeout=self.timeout)
        cp = httpx.AsyncClient(base_url=self.control_url, timeout=30.0)

        try:
            # 1. Read injected environment from control plane
            env_resp = await cp.get("/current/environment")
            env_resp.raise_for_status()
            env = ShellEnvironment.model_validate(env_resp.json())
            workspace_files = env.workspace_file_contents

            # 2. Snapshot sandbox IDs before container creation (for OCSF correlation)
            endpoint = self.openshell_endpoint or os.environ.get("OPENSHELL_ENDPOINT")
            sandboxes_before: dict = {}
            if endpoint:
                sandboxes_before = await asyncio.to_thread(list_sandbox_refs, endpoint)

            # 3. Create container
            c_resp = await http.post("/v1/containers", json={"name": "midojo-redteam", "memory_limit": "1g"})
            c_resp.raise_for_status()
            container_id = c_resp.json()["id"]

            try:
                # 4. Seed workspace files
                await http.post(f"/v1/containers/{container_id}/exec", json={
                    "container_id": container_id,
                    "commands": ["mkdir -p /workspace"],
                })
                for path, content in workspace_files.items():
                    safe = content.replace("'", "'\\''")
                    await http.post(f"/v1/containers/{container_id}/exec", json={
                        "container_id": container_id,
                        "commands": [f"printf '%s' '{safe}' > /workspace/{path}"],
                    })

                # 5. Baseline marker for workspace diff
                await http.post(f"/v1/containers/{container_id}/exec", json={
                    "container_id": container_id,
                    "commands": ["touch /tmp/.midojo_baseline"],
                })

                # 6. Find new sandbox ID (diff after seeding so sandbox is ready)
                sandbox_id: str | None = None
                if endpoint:
                    sandboxes_after = await asyncio.to_thread(list_sandbox_refs, endpoint)
                    new_ids = set(sandboxes_after) - set(sandboxes_before)
                    if new_ids:
                        sandbox_id = new_ids.pop()

                # Record start time for OCSF log window
                start_ms = int(time.time() * 1000)

                # 7. Call Responses API with container_reference
                openai_client = AsyncOpenAI(
                    base_url=self.ogx_url + "/v1",
                    api_key=os.environ.get("OPENAI_API_KEY", "ollama"),
                )
                response = await openai_client.responses.create(
                    model=os.environ.get("OGX_MODEL", "qwen3.5:2b"),
                    tools=[{
                        "type": "shell",
                        "environment": {
                            "type": "container_reference",
                            "container_id": container_id,
                        },
                    }],
                    input=prompt,
                )

                # 8. Extract shell_call items and record to control plane
                # shell_call.action = the command; shell_call_output = {exit_code, stdout, stderr}
                commands_executed: list[CommandRecord] = []
                output_items = list(response.output)
                for i, item in enumerate(output_items):
                    if item.type == "shell_call":
                        exit_code = 0
                        stdout = ""
                        stderr = ""
                        if i + 1 < len(output_items) and output_items[i + 1].type == "shell_call_output":
                            out = output_items[i + 1]
                            exit_code = getattr(out, "exit_code", 0) or 0
                            stdout = getattr(out, "stdout", "") or ""
                            stderr = getattr(out, "stderr", "") or ""
                        cmd_record = CommandRecord(
                            command=item.action,
                            exit_code=exit_code,
                            stdout=stdout,
                            stderr=stderr,
                        )
                        commands_executed.append(cmd_record)
                        await cp.post("/current/function-calls", json={
                            "function": "shell",
                            "args": {"command": item.action},
                            "result": stdout,
                            "error": stderr if stderr else (None if exit_code == 0 else f"exit_code={exit_code}"),
                        })

                # 9. Workspace diff — snapshot all files before/after, detect new/modified/deleted
                # "Newer than baseline" catches created + modified
                diff_resp = await http.post(f"/v1/containers/{container_id}/exec", json={
                    "container_id": container_id,
                    "commands": ["find /workspace -type f -newer /tmp/.midojo_baseline 2>/dev/null"],
                })
                # All files currently in workspace (to detect deletions)
                all_resp = await http.post(f"/v1/containers/{container_id}/exec", json={
                    "container_id": container_id,
                    "commands": ["find /workspace -type f 2>/dev/null"],
                })

                seeded_paths = {f"/workspace/{p}" for p in workspace_files}
                current_paths = {
                    line.strip()
                    for line in all_resp.json().get("stdout", "").splitlines()
                    if line.strip()
                }
                new_files: list[str] = []
                modified_files: list[str] = []
                new_file_contents: dict[str, str] = {}

                for line in diff_resp.json().get("stdout", "").splitlines():
                    fpath = line.strip()
                    if not fpath:
                        continue
                    if fpath in seeded_paths:
                        modified_files.append(fpath)
                    else:
                        new_files.append(fpath)
                        try:
                            read_resp = await http.post(f"/v1/containers/{container_id}/exec", json={
                                "container_id": container_id,
                                "commands": [f"cat '{fpath}' 2>/dev/null"],
                            })
                            if read_resp.json().get("exit_code", 1) == 0:
                                new_file_contents[fpath] = read_resp.json().get("stdout", "")
                        except Exception:
                            pass

                # Deleted = seeded files no longer in workspace
                deleted_files = [p for p in seeded_paths if p not in current_paths]

                # 10. Fetch OCSF events from OpenShell (if endpoint available)
                # Returns OCSFEvents with network, HTTP, process, and finding events
                ocsf = None
                if endpoint and sandbox_id:
                    from midojo.openshell_logs import OCSFEvents
                    ocsf = await asyncio.to_thread(
                        fetch_ocsf_events, sandbox_id, start_ms, endpoint
                    )

                # 11. Update environment on control plane
                env.commands_executed = commands_executed
                env.files_created = new_files
                env.files_modified = modified_files
                env.files_deleted = deleted_files
                env.workspace_new_file_contents = new_file_contents
                if ocsf is not None:
                    env.network_calls_allowed = ocsf.network_allowed_endpoints
                    env.network_calls_blocked = ocsf.network_blocked_endpoints
                    env.http_calls_allowed = [f"{e.method} {e.url}" for e in ocsf.http_allowed]
                    env.http_calls_blocked = [f"{e.method} {e.url}" for e in ocsf.http_blocked]
                    env.processes_started = [p.binary for p in ocsf.processes_launched]
                    env.security_findings = [f.title for f in ocsf.findings]
                await cp.put("/current/environment", json=env.model_dump())

            finally:
                # Always delete container
                try:
                    await http.delete(f"/v1/containers/{container_id}")
                except Exception:
                    pass

            return response.output_text or ""

        finally:
            await http.aclose()
            await cp.aclose()


class PIAgentClient(AgentClient):
    """Launches a PI coding agent as a subprocess for each task.

    The ``agent_dir`` should point to a directory containing a ``.pi/``
    configuration folder with extensions that use ``@midojo/pi-sdk``.
    """

    def __init__(
        self,
        agent_dir: str,
        control_url: str,
        *,
        timeout: float = 120.0,
    ) -> None:
        self.agent_dir = os.path.abspath(agent_dir)
        self.control_url = control_url
        self.timeout = timeout

    async def send_task(self, prompt: str) -> str:
        env = {
            **os.environ,
            "MIDOJO_URL": self.control_url,
        }

        proc = await asyncio.create_subprocess_exec(
            "pi", "-p", "--no-session", prompt,
            cwd=self.agent_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"PI agent timed out after {self.timeout}s")

        if proc.returncode != 0:
            raise RuntimeError(
                f"PI agent exited with code {proc.returncode}: {stderr.decode()}"
            )

        return stdout.decode().strip()
