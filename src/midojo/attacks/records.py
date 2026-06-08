"""Catalog record shapes for the attack library.

The library holds two kinds of entry (issue #36):

* **Vehicles** — *how* you attack: a `Callable[[str], str]` that wraps a payload
  (the cargo) in a delivery technique. This is the cargo/vehicle split midojo
  already uses in `attacks.registry`.
* **Payload sets** — *what* you say: a corpus of payload strings (e.g. Garak's
  hijack strings) that a suite probe samples from.

Only vehicles are wired up today; `PayloadSet` is scaffolded here so the
registry, taxonomy, and provenance metadata are shared by both kinds when
payload-set sourcing lands.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field

from midojo.attacks.taxonomy import validate_asi_code

# A vehicle: take a payload (cargo), return the wrapped payload.
AttackType = Callable[[str], str]


class AttackKind(enum.StrEnum):
    VEHICLE = "vehicle"
    PAYLOAD_SET = "payload_set"


@dataclass(frozen=True)
class AttackTechnique:
    """A vehicle entry: a reusable wrapping technique plus its provenance.

    ``id`` is the name suites reference via a probe's ``attack_type``.
    ``source`` records provenance (e.g. ``"builtin"`` or
    ``"agentdojo:important_instructions"``) so imported attacks keep
    attribution. ``owasp_asi`` tags index the entry into the curation spine.
    """

    id: str
    wrap: AttackType
    description: str
    source: str = "builtin"
    owasp_asi: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    license: str | None = None
    kind: AttackKind = field(default=AttackKind.VEHICLE, init=False)

    def __post_init__(self) -> None:
        for code in self.owasp_asi:
            validate_asi_code(code)
