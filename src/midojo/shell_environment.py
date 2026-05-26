"""ShellEnvironment — task environment for shell-executing agents.

The workspace_file_contents dict holds seeded file contents (with injection
placeholders already substituted). The ShellOGXAgentClient reads these at
evaluation time and writes them to the container workspace before invoking
the Responses API.

Post-session fields (commands_executed, files_created, files_modified,
network_calls_allowed, network_calls_blocked) are populated by the agent
client and used by shell-specific grading predicates.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agentdojo.functions_runtime import TaskEnvironment


class CommandRecord(BaseModel):
    command: str
    exit_code: int
    stdout: str


class ShellEnvironment(TaskEnvironment):
    # Seeded file contents — keys are paths relative to /workspace, values are
    # the full file content (injections already substituted at eval creation).
    workspace_file_contents: dict[str, str] = Field(default_factory=dict)

    # Populated by ShellOGXAgentClient after the Responses API call.
    commands_executed: list[CommandRecord] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)

    # Contents of files created by the agent during the session (not seeded by us).
    # Keys are full paths (e.g. "/workspace/.hidden"), values are file contents.
    # Populated in Phase 2 during workspace diff.
    workspace_new_file_contents: dict[str, str] = Field(default_factory=dict)

    # Populated in Phase 2 via OpenShell OCSF log parsing.
    network_calls_allowed: list[str] = Field(default_factory=list)
    network_calls_blocked: list[str] = Field(default_factory=list)
