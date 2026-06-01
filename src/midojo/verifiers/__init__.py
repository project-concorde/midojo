"""Verifier discovery and bootstrap."""

from __future__ import annotations

from midojo.verifier import Verifier

from .builtin import BuiltinVerifier
from .rhacs import RhacsVerifier

_KNOWN_VERIFIERS: list[type[Verifier]] = [BuiltinVerifier, RhacsVerifier]


def discover_verifiers() -> dict[str, Verifier]:
    """Instantiate verifiers that are configured via environment variables, keyed by name."""
    verifiers: dict[str, Verifier] = {}
    for cls in _KNOWN_VERIFIERS:
        instance = cls.from_env()
        if instance is not None:
            verifiers[instance.name] = instance
    return verifiers


def bootstrap_verifiers() -> dict[str, Verifier]:
    """Register predicate parsers from all known verifiers, then discover configured instances.

    Call this before loading any suite so that verifier-contributed
    predicate types are available to parse_predicate().
    """
    from midojo.predicates import register_predicate_parser

    for cls in _KNOWN_VERIFIERS:
        for key, parser in cls.predicate_parsers().items():
            register_predicate_parser(key, parser)
    return discover_verifiers()
