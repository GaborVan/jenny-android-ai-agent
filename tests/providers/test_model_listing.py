"""La lista modelli del bridge: come si legge ``GET /v1/models``.

Il bridge espone i CLI come modelli (``codex``/``claude``/``gemini``), ma la
forma del payload cambia: l'API OpenAI incapsula in ``data[].id``, alcuni server
in ``models[].name``, altri rispondono con la lista nuda. La tendina della UI
deve mostrare gli stessi modelli che il bridge offre, una volta sola, nell'ordine
in cui il bridge li ha messi — che è l'unico suggerimento su quale preferire.
"""

from __future__ import annotations

from jenny.providers.model_listing import model_id_from_row, model_rows, parse_model_ids

OPENAI_STYLE = {
    "object": "list",
    "data": [
        {"id": "codex", "object": "model", "owned_by": "bridge"},
        {"id": "claude", "object": "model", "owned_by": "bridge"},
        {"id": "gemini", "object": "model", "owned_by": "bridge"},
    ],
}


def test_the_openai_shape_is_parsed_in_order() -> None:
    assert parse_model_ids(OPENAI_STYLE) == ["codex", "claude", "gemini"]


def test_the_models_key_is_accepted_too() -> None:
    assert parse_model_ids({"models": [{"name": "codex"}, {"name": "claude"}]}) == [
        "codex",
        "claude",
    ]


def test_a_bare_list_is_accepted() -> None:
    assert parse_model_ids(["codex", "claude"]) == ["codex", "claude"]


def test_duplicates_are_dropped_and_the_first_position_wins() -> None:
    payload = {"data": [{"id": "codex"}, {"id": "claude"}, {"id": "codex"}]}

    assert parse_model_ids(payload) == ["codex", "claude"]


def test_junk_rows_are_skipped_instead_of_becoming_model_names() -> None:
    payload = {"data": [{"id": "codex"}, 7, [], {"nope": "x"}, {"id": "   "}, "claude"]}

    assert parse_model_ids(payload) == ["codex", "claude"]


def test_an_unexpected_payload_yields_no_models() -> None:
    # Un payload inatteso è "nessun modello", che la UI sa già mostrare: non è
    # un errore da far scoppiare in faccia a chi legge le impostazioni.
    assert parse_model_ids(None) == []
    assert parse_model_ids("oops") == []
    assert parse_model_ids({"error": "unauthorized"}) == []
    assert parse_model_ids(42) == []


def test_ids_are_stripped() -> None:
    assert model_id_from_row("  codex  ") == "codex"
    assert model_id_from_row({"id": " codex "}) == "codex"
    assert model_id_from_row({"name": "claude"}) == "claude"
    assert model_id_from_row({"model": "gemini"}) == "gemini"
    assert model_id_from_row({"id": ""}) is None
    assert model_id_from_row(None) is None


def test_rows_are_read_from_data_then_models_then_the_list_itself() -> None:
    assert model_rows(OPENAI_STYLE) == OPENAI_STYLE["data"]
    assert model_rows({"models": ["codex"]}) == ["codex"]
    assert model_rows(["codex"]) == ["codex"]
    assert model_rows({"unrelated": []}) == []
