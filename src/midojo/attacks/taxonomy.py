"""OWASP Agentic Security Initiative (ASI) threat taxonomy.

The canonical T1-T15 threat codes from the OWASP ASI "Agentic AI - Threats
and Mitigations" work. Attack-library entries are tagged with these codes so
the catalog can be queried by threat class (e.g. "all techniques covering
T6 — Intent Breaking & Goal Manipulation").

This is the curation spine: importing attacks from AgentDojo/Garak maps their
native categories onto these codes, and hand-curation is only the delta.
"""

from __future__ import annotations

# code -> human-readable threat name
OWASP_ASI_THREATS: dict[str, str] = {
    "T1": "Memory Poisoning",
    "T2": "Tool Misuse",
    "T3": "Privilege Compromise",
    "T4": "Resource Overload",
    "T5": "Cascading Hallucination Attacks",
    "T6": "Intent Breaking & Goal Manipulation",
    "T7": "Misaligned & Deceptive Behaviors",
    "T8": "Repudiation & Untraceability",
    "T9": "Identity Spoofing & Impersonation",
    "T10": "Overwhelming Human-in-the-Loop",
    "T11": "Unexpected RCE & Code Attacks",
    "T12": "Agent Communication Poisoning",
    "T13": "Rogue Agents in Multi-Agent Systems",
    "T14": "Human Attacks on Multi-Agent Systems",
    "T15": "Human Manipulation",
}


def validate_asi_code(code: str) -> str:
    """Return ``code`` if it is a known OWASP ASI threat code, else raise."""
    if code not in OWASP_ASI_THREATS:
        raise ValueError(
            f"Unknown OWASP ASI threat code '{code}'. Known codes: {sorted(OWASP_ASI_THREATS)}"
        )
    return code
