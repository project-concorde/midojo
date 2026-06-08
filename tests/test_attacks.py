import pytest

from midojo.attacks import (
    DEFAULT_LIBRARY,
    AttackLibrary,
    AttackTechnique,
    wrap_payload,
)


class TestDefaultLibrary:
    def test_builtin_vehicles_present(self):
        assert set(DEFAULT_LIBRARY.ids()) >= {"verbatim", "important_instructions", "ignore_previous"}

    def test_verbatim_passes_payload_through(self):
        assert wrap_payload("hello", "verbatim") == "hello"

    def test_important_instructions_wraps_payload(self):
        wrapped = wrap_payload("do the thing", "important_instructions")
        assert "do the thing" in wrapped
        assert "Emma Johnson" in wrapped

    def test_unknown_attack_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported attack_type 'nope'"):
            wrap_payload("x", "nope")


class TestProvenanceAndTaxonomy:
    def test_important_instructions_attributed_to_agentdojo(self):
        assert DEFAULT_LIBRARY.get("important_instructions").source == "agentdojo:important_instructions"

    def test_query_by_asi_code(self):
        ids = {t.id for t in DEFAULT_LIBRARY.by_asi("T6")}
        assert ids == {"important_instructions", "ignore_previous"}

    def test_unknown_asi_code_rejected_at_construction(self):
        with pytest.raises(ValueError, match="Unknown OWASP ASI threat code"):
            AttackTechnique(id="bad", wrap=lambda p: p, description="x", owasp_asi=("T99",))


class TestAttackLibrary:
    def test_duplicate_registration_raises(self):
        lib = AttackLibrary([AttackTechnique(id="v", wrap=lambda p: p, description="x")])
        with pytest.raises(ValueError, match="already registered"):
            lib.register(AttackTechnique(id="v", wrap=lambda p: p, description="y"))
