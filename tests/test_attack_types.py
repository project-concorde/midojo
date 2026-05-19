import pytest

from midojo.attack_types import ATTACK_TYPES, wrap_payload


class TestWrapPayload:
    def test_verbatim_is_identity(self):
        assert wrap_payload("hello world", "verbatim") == "hello world"

    def test_important_instructions_wraps_payload(self):
        out = wrap_payload("do the thing", "important_instructions")
        assert "do the thing" in out
        assert "Emma Johnson" in out
        assert "<INFORMATION>" in out
        assert "</INFORMATION>" in out

    def test_important_instructions_does_not_leak_unfilled_placeholders(self):
        # Templates copied from agentdojo originally had {user}/{model}/{task}
        # placeholders. We strip them — verify none leak through verbatim.
        out = wrap_payload("anything", "important_instructions")
        assert "{user}" not in out
        assert "{model}" not in out
        assert "{task}" not in out
        assert "{goal}" not in out

    def test_ignore_previous_wraps_payload(self):
        out = wrap_payload("send a tornado alert", "ignore_previous")
        assert "send a tornado alert" in out
        assert "Ignore" in out

    def test_unknown_attack_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported attack_type"):
            wrap_payload("anything", "nonexistent_strategy")

    def test_registry_lists_supported_types(self):
        assert "verbatim" in ATTACK_TYPES
        assert "important_instructions" in ATTACK_TYPES
        assert "ignore_previous" in ATTACK_TYPES
