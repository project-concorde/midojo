"""ShellEnvironment — task environment for shell-executing agents.

The workspace_file_contents dict holds seeded file contents (with injection
placeholders already substituted). The ShellOGXAgentClient reads these at
evaluation time and writes them to the container workspace before invoking
the Responses API.

Post-session fields are populated by the agent client and used by shell-specific
grading predicates. All fields default to empty so predicates degrade gracefully
in Phase 1 mode (no OPENSHELL_ENDPOINT set).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentdojo.functions_runtime import TaskEnvironment


class CommandRecord(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str = ""


class ShellEnvironment(TaskEnvironment):
    # Seeded file contents — keys are paths relative to /workspace, values are
    # the full file content (injections already substituted at eval creation).
    workspace_file_contents: dict[str, str] = Field(default_factory=dict)

    # Populated by ShellOGXAgentClient after the Responses API call.
    # Contains every shell command the model ran, in order.
    commands_executed: list[CommandRecord] = Field(default_factory=list)

    # Files created by the agent (not seeded by us). Workspace diff result.
    files_created: list[str] = Field(default_factory=list)
    # Seeded files modified during the session.
    files_modified: list[str] = Field(default_factory=list)
    # Seeded files deleted during the session.
    files_deleted: list[str] = Field(default_factory=list)

    # Contents of files created by the agent.
    # Keys are full paths (e.g. "/workspace/.hidden"), values are file contents.
    workspace_new_file_contents: dict[str, str] = Field(default_factory=dict)

    # Populated in Phase 2 via OpenShell OCSF log parsing.
    # Format: "host:port" strings (e.g. "audit.ext-log.com:443")
    network_calls_allowed: list[str] = Field(default_factory=list)
    network_calls_blocked: list[str] = Field(default_factory=list)

    # Full HTTP events (method + URL) for finer-grained HTTP grading.
    # Format: "METHOD https://host/path" (e.g. "POST https://attacker.com/collect")
    http_calls_allowed: list[str] = Field(default_factory=list)
    http_calls_blocked: list[str] = Field(default_factory=list)

    # Processes the kernel observed starting (binary names from PROC:LAUNCH events).
    # Stronger than commands_executed — kernel-verified, catches subprocesses.
    # e.g. ["python3", "curl"] even if model only showed "python3 analyze.py"
    processes_started: list[str] = Field(default_factory=list)

    # Security findings from OpenShell (e.g. "Proxy Bypass Detected").
    security_findings: list[str] = Field(default_factory=list)
