from __future__ import annotations

from jenny.providers.openai_compat_helpers import (
    _requires_max_completion_tokens,
    is_openai_reasoning_model,
)
from jenny.providers.openai_compat_provider import OpenAICompatProvider


def test_recognizes_openai_reasoning_families() -> None:
    for model in ("gpt-5", "gpt-5-mini", "o1", "o1-preview", "o3", "o3-mini", "o4-mini"):
        assert is_openai_reasoning_model(model), model


def test_recognizes_reasoning_families_with_vendor_prefix() -> None:
    assert is_openai_reasoning_model("openai/o3-mini")
    assert is_openai_reasoning_model("openai/gpt-5")


def test_rejects_third_party_substring_false_positives() -> None:
    # Il vecchio substring matching (``"o1" in name``) classificava questi
    # modelli come reasoning: il match slug-based non deve farlo.
    for model in ("some-o1-lookalike", "yi-o1-chat", "no3-turbo", "go4it-model"):
        assert not is_openai_reasoning_model(model), model
    assert not is_openai_reasoning_model("gpt-4o")


def test_requires_max_completion_tokens_matches_reasoning_predicate() -> None:
    for model in ("gpt-5", "o1", "o3-mini", "o4-mini", "gpt-4o", "yi-o1-chat"):
        assert _requires_max_completion_tokens(model) == is_openai_reasoning_model(model)


def test_supports_temperature_uses_slug_matching() -> None:
    # Un modello di terze parti che contiene 'o1' nel nome NON è reasoning e
    # deve continuare ad accettare temperature.
    assert OpenAICompatProvider._supports_temperature("yi-o1-chat", None) is True
    assert OpenAICompatProvider._supports_temperature("gpt-4o", None) is True
    assert OpenAICompatProvider._supports_temperature("o3-mini", None) is False
    assert OpenAICompatProvider._supports_temperature("gpt-5", None) is False
