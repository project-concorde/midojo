"""Fetch and parse OpenShell OCSF log events via gRPC proto stubs.

Usage:
    from midojo.openshell_logs import list_sandbox_ids, fetch_ocsf_events

OCSF events arrive in GetSandboxLogs as SandboxLogLine with level="OCSF" and
message strings like "CONNECT ALLOWED pypi.org:443" or "CONNECT DENIED evil.com:443".
The fields map is empty for OCSF events — parsing is done on the message string.

Sandbox ID correlation hack:
    OGX's Containers API doesn't expose the underlying OpenShell sandbox ID.
    We correlate by calling SandboxClient.list() before and after container
    creation, then diffing. The new SandboxRef is ours.
    This breaks under concurrent evaluations — a proper fix would contribute
    sandbox_id to OGX's Container model response (one-line change to containers.py
    _row_to_container in the ogx fork).
"""

from __future__ import annotations

import re
import time

_CONNECT_PATTERN = re.compile(
    r"CONNECT\s+(ALLOWED|DENIED)\s+([\w.\-]+):(\d+)", re.IGNORECASE
)


def _make_stub(gateway_endpoint: str):
    """Create an OpenShellStub from the gateway endpoint."""
    import grpc
    from openshell._proto import openshell_pb2_grpc  # type: ignore[import]

    channel = grpc.insecure_channel(gateway_endpoint)
    return openshell_pb2_grpc.OpenShellStub(channel), channel


def list_sandbox_refs(gateway_endpoint: str) -> dict[str, object]:
    """Return {sandbox_id: SandboxRef} for all current sandboxes.

    Returns empty dict if openshell package is not installed.
    """
    try:
        from openshell import SandboxClient  # type: ignore[import]
    except ImportError:
        return {}

    try:
        client = SandboxClient(gateway_endpoint)
        return {ref.id: ref for ref in client.list()}
    except Exception:
        return {}


def fetch_ocsf_events(
    sandbox_id: str,
    start_ms: int,
    gateway_endpoint: str,
) -> tuple[list[str], list[str]]:
    """Fetch OCSF network activity events for a sandbox since start_ms.

    Returns (allowed, blocked) where each is a list of "host:port" strings
    parsed from OCSF CONNECT ALLOWED / CONNECT DENIED log messages.

    Returns ([], []) if openshell package is not installed or on any error.
    The grading predicates (NetworkCallTo, NetworkCallBlockedTo) treat missing
    data as False, so Phase 1 suites remain unaffected.
    """
    try:
        from openshell._proto import openshell_pb2  # type: ignore[import]
    except ImportError:
        return [], []

    stub, channel = _make_stub(gateway_endpoint)
    try:
        response = stub.GetSandboxLogs(
            openshell_pb2.GetSandboxLogsRequest(
                sandbox_id=sandbox_id,
                since_ms=start_ms,
                sources=["sandbox"],  # OCSF events come from the sandbox supervisor
            ),
            timeout=10.0,
        )
    except Exception:
        return [], []
    finally:
        channel.close()

    allowed: list[str] = []
    blocked: list[str] = []

    for log_line in response.logs:
        if log_line.level.upper() != "OCSF":
            continue
        m = _CONNECT_PATTERN.search(log_line.message)
        if m:
            action, host, port = m.groups()
            endpoint = f"{host}:{port}"
            if action.upper() == "ALLOWED":
                allowed.append(endpoint)
            else:
                blocked.append(endpoint)

    return allowed, blocked
