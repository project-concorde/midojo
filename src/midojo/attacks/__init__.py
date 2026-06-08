"""MiDojo's attack library: the catalog of attack techniques that get
templated into suites at evaluation time.

It separates *what* you're testing (suite authoring) from *how* you're
attacking (this library), so suites stay stable while attacks evolve. See
issue #36 for the design.
"""

from __future__ import annotations

from midojo.attacks.records import AttackKind, AttackTechnique, AttackType
from midojo.attacks.registry import DEFAULT_LIBRARY, AttackLibrary, wrap_payload
from midojo.attacks.taxonomy import OWASP_ASI_THREATS

__all__ = [
    "DEFAULT_LIBRARY",
    "OWASP_ASI_THREATS",
    "AttackKind",
    "AttackLibrary",
    "AttackTechnique",
    "AttackType",
    "wrap_payload",
]
