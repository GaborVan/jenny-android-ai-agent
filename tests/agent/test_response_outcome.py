"""Classificazione dell'esito di una risposta del modello.

Il classificatore è puro, quindi la matrice
``(finish_reason x content x usage)`` si copre per intero a costo zero. È il
punto in cui si decide quale recovery scatta: un buco qui è un turno che gira
a vuoto per minuti, come nel caso del troncamento dentro il thinking.
"""

import pytest

from jenny.agent.response_outcome import (
    ResponseOutcome,
    classify_response,
    output_budget_exhausted,
    reported_completion_tokens,
)
from jenny.providers.base import LLMResponse, ToolCallRequest


def _response(**kwargs) -> LLMResponse:
    kwargs.setdefault("content", None)
    return LLMResponse(**kwargs)


# --- reported_completion_tokens ------------------------------------------------


def test_reported_completion_tokens_reads_provider_usage():
    response = _response(usage={"prompt_tokens": 100, "completion_tokens": 8192})
    assert reported_completion_tokens(response) == 8192


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"prompt_tokens": 100},
        {"completion_tokens": 0},
    ],
)
def test_reported_completion_tokens_absent_is_none(usage):
    """Usage mancante o nullo non deve diventare uno 0 che sembra un dato."""
    assert reported_completion_tokens(_response(usage=usage)) is None


# --- output_budget_exhausted ---------------------------------------------------


def test_finish_reason_length_is_enough():
    """Primo signal: il provider lo dichiara."""
    assert output_budget_exhausted(_response(finish_reason="length"), None) is True


def test_usage_at_cap_detected_even_when_provider_says_stop():
    """Secondo signal, quello che regge il caso reale.

    Non tutti i gateway OpenAI-compatibili riportano ``length`` sul troncamento.
    Senza questo confronto il fix non scatterebbe proprio sui provider che
    etichettano male la risposta.
    """
    response = _response(finish_reason="stop", usage={"completion_tokens": 8192})
    assert output_budget_exhausted(response, 8192) is True


def test_usage_over_cap_detected():
    response = _response(finish_reason="stop", usage={"completion_tokens": 8200})
    assert output_budget_exhausted(response, 8192) is True


def test_one_token_below_cap_still_counts_as_exhausted():
    """Caso osservato sul device: troncamento reale riportato a ``tetto - 1``.

    Con un confronto esatto il signal sull'usage sarebbe muto per un token, e
    resterebbe in piedi solo grazie a ``finish_reason``.
    """
    response = _response(finish_reason="stop", usage={"completion_tokens": 8191})
    assert output_budget_exhausted(response, 8192) is True


def test_usage_clearly_below_cap_is_a_legitimate_stop():
    """La tolleranza è una banda stretta, non un allargamento della soglia."""
    response = _response(finish_reason="stop", usage={"completion_tokens": 8000})
    assert output_budget_exhausted(response, 8192) is False


def test_slack_shrinks_on_tiny_caps():
    """Su tetti minuscoli la tolleranza si annulla invece di coprire tutto."""
    response = _response(finish_reason="stop", usage={"completion_tokens": 20})
    assert output_budget_exhausted(response, 32) is False
    assert output_budget_exhausted(_response(
        finish_reason="stop", usage={"completion_tokens": 32}
    ), 32) is True


def test_unknown_cap_falls_back_to_finish_reason_only():
    response = _response(finish_reason="stop", usage={"completion_tokens": 99999})
    assert output_budget_exhausted(response, None) is False
    assert output_budget_exhausted(response, 0) is False


# --- classify_response ---------------------------------------------------------


def test_blank_truncation_is_its_own_outcome():
    """Il caso che cadeva nel ramo sbagliato: budget bruciato nel thinking.

    ``content`` vuoto perché il reasoning si è mangiato tutto il budget. Prima
    veniva classificato come "risposta vuota" e ritentato identico.
    """
    response = _response(
        finish_reason="length",
        reasoning_content="x" * 500,
        usage={"completion_tokens": 8192},
    )
    assert classify_response(response, None, max_tokens=8192) is (
        ResponseOutcome.TRUNCATED_BLANK
    )


def test_blank_truncation_covers_partial_tool_calls():
    """Troncamento a metà dei tool call: stesso buco, stessa classificazione.

    ``should_execute_tools`` scarta giustamente i tool call sotto ``length``
    (argomenti JSON incompleti), lasciando una risposta senza contenuto utile.
    """
    response = _response(
        finish_reason="length",
        tool_calls=[ToolCallRequest(id="1", name="write_file", arguments={})],
        usage={"completion_tokens": 8192},
    )
    assert response.should_execute_tools is False
    assert classify_response(response, None, max_tokens=8192) is (
        ResponseOutcome.TRUNCATED_BLANK
    )


def test_whitespace_only_content_counts_as_blank():
    response = _response(finish_reason="length", usage={"completion_tokens": 8192})
    assert classify_response(response, "   \n ", max_tokens=8192) is (
        ResponseOutcome.TRUNCATED_BLANK
    )


def test_truncation_with_text_stays_continuable():
    response = _response(finish_reason="length", usage={"completion_tokens": 8192})
    assert classify_response(response, "partial answer", max_tokens=8192) is (
        ResponseOutcome.TRUNCATED_WITH_TEXT
    )


def test_genuinely_empty_response_is_not_truncation():
    """Non-regressione: la risposta vuota senza troncamento resta EMPTY.

    È il caso in cui il retry cieco è legittimo, e non deve finire nel recovery
    del troncamento (che alzerebbe il budget senza motivo).
    """
    response = _response(finish_reason="stop", usage={"completion_tokens": 1})
    assert classify_response(response, None, max_tokens=8192) is ResponseOutcome.EMPTY


def test_ok_response():
    response = _response(
        content="answer", finish_reason="stop", usage={"completion_tokens": 12}
    )
    assert classify_response(response, "answer", max_tokens=8192) is ResponseOutcome.OK


def test_context_overflow_is_distinct_from_generic_error():
    response = _response(
        content="Error: context_length_exceeded",
        finish_reason="error",
        error_code="context_length_exceeded",
    )
    assert classify_response(response, None, max_tokens=8192) is (
        ResponseOutcome.CONTEXT_OVERFLOW
    )


def test_generic_error():
    response = _response(content="Error: boom", finish_reason="error")
    assert classify_response(response, None, max_tokens=8192) is ResponseOutcome.ERROR


def test_error_wins_over_truncation_signals():
    """Un errore non va letto come troncamento anche se l'usage è al tetto."""
    response = _response(
        content="Error: boom",
        finish_reason="error",
        usage={"completion_tokens": 8192},
    )
    assert classify_response(response, None, max_tokens=8192) is ResponseOutcome.ERROR
