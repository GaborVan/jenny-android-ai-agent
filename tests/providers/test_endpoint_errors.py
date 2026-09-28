"""La diagnosi di un guasto provider: token, backend, rete — in una frase.

Il caso che ha motivato il modulo: il bridge di casa risponde 401 se il token è
sbagliato, 503 se il CLI dietro ha esaurito la sessione, e non risponde affatto
se sul telefono Tailscale è spento. Tre guasti, un solo messaggio grezzo prima
d'ora — e un utente che controlla la cosa sbagliata.

Questi test pinnano la classificazione, il testo ucraino del caso Tailscale
(parola per parola: è quello che l'utente legge), e il fatto che il provider lo
usi al posto del payload solo quando una diagnosi c'è davvero.
"""

from __future__ import annotations

import pytest

from jenny.providers.endpoint_errors import (
    CATEGORY_AUTH,
    CATEGORY_BACKEND,
    CATEGORY_NETWORK,
    CATEGORY_TIMEOUT,
    CATEGORY_UNKNOWN,
    TAILSCALE_HINT,
    EndpointDiagnosis,
    classify_endpoint_error,
    classify_exception,
    diagnose_for_user,
    is_tailscale_host,
)
from jenny.providers.openai_compat_provider import OpenAICompatProvider

BRIDGE_BASE = "https://gaborvan-nitro-an515-42-1.taild5ff76.ts.net:8899/v1"


# ── Il riconoscimento dell'host ────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        BRIDGE_BASE,
        "https://host.taild5ff76.ts.net/v1",
        "host.taild5ff76.ts.net:8899/v1",
        "https://HOST.TAILD5FF76.TS.NET/v1",
    ],
)
def test_a_tailscale_host_is_recognized(url: str) -> None:
    assert is_tailscale_host(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "https://api.openai.com/v1",
        "http://192.168.1.9:8080/v1",
        "https://ts.net.evil.example/v1",
        "https://taild5ff76.ts.net.evil.example/v1",
    ],
)
def test_everything_else_is_not_tailscale(url: str | None) -> None:
    # ``ts.net.evil.example`` non è un host Tailscale: il suffisso si controlla
    # in fondo, non "contiene".
    assert is_tailscale_host(url) is False


# ── La classificazione ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_is_an_auth_failure(status: int) -> None:
    diagnosis = classify_endpoint_error(status_code=status, api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_AUTH
    assert str(status) in diagnosis.message
    assert "Токен" in diagnosis.message
    # Un 401 dice che il VPN funziona: niente suggerimento Tailscale.
    assert TAILSCALE_HINT not in diagnosis.text()


@pytest.mark.parametrize("status", [500, 502, 503, 504, 529])
def test_a_server_error_is_a_backend_failure(status: int) -> None:
    diagnosis = classify_endpoint_error(status_code=status, api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_BACKEND
    assert str(status) in diagnosis.message
    assert diagnosis.status_code == status
    assert "ліміт" in (diagnosis.hint or "")
    # Il bridge ha risposto: la rete ha funzionato, e mandare a controllare il
    # VPN qui sarebbe la diagnosi sbagliata.
    assert TAILSCALE_HINT not in diagnosis.text()


def test_an_unknown_client_error_keeps_its_status() -> None:
    diagnosis = classify_endpoint_error(status_code=400, api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_UNKNOWN
    assert diagnosis.status_code == 400


@pytest.mark.parametrize("kind", [None, "connection"])
def test_no_response_from_a_tailscale_host_blames_the_vpn(kind: str | None) -> None:
    diagnosis = classify_endpoint_error(error_kind=kind, api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_NETWORK
    assert "Не вдалося з'єднатися" in diagnosis.message
    assert diagnosis.hint == TAILSCALE_HINT
    # Il testo richiesto, parola per parola: è quello che finisce in chat.
    assert (
        "Схоже, Tailscale вимкнено або недоступний. Увімкніть VPN-з'єднання "
        "Tailscale на телефоні (і переконайтеся, що MagicDNS увімкнений)."
    ) == TAILSCALE_HINT
    assert TAILSCALE_HINT in diagnosis.text()


def test_a_timeout_on_a_tailscale_host_blames_the_vpn() -> None:
    diagnosis = classify_endpoint_error(error_kind="timeout", api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_TIMEOUT
    assert diagnosis.hint == TAILSCALE_HINT


def test_no_response_from_a_plain_host_has_no_tailscale_hint() -> None:
    diagnosis = classify_endpoint_error(api_base="https://api.openai.com/v1")

    assert diagnosis.category == CATEGORY_NETWORK
    assert diagnosis.hint is None
    assert "Tailscale" not in diagnosis.text()


def test_an_unreadable_status_is_treated_as_no_response() -> None:
    diagnosis = classify_endpoint_error(status_code="not-a-status", api_base=BRIDGE_BASE)

    assert diagnosis.category == CATEGORY_NETWORK


def test_the_text_joins_message_and_hint() -> None:
    joined = EndpointDiagnosis(CATEGORY_AUTH, "a", hint="b").text()
    alone = EndpointDiagnosis(CATEGORY_AUTH, "a").text()

    assert joined == "a b"
    assert alone == "a"


# ── Dal trasporto all'eccezione ────────────────────────────────────────────


class ConnectError(Exception):
    """Il nome della classe è l'unico dato che ``classify_exception`` legge."""


class _HttpError(Exception):
    def __init__(self, status: int, body: str = "upstream said no") -> None:
        super().__init__("boom")
        self.status_code = status
        self.body = body


def test_status_and_kind_are_read_off_the_exception() -> None:
    assert classify_exception(_HttpError(401), api_base=BRIDGE_BASE).category == CATEGORY_AUTH
    assert classify_exception(_HttpError(503), api_base=BRIDGE_BASE).category == CATEGORY_BACKEND
    assert classify_exception(ConnectError(), api_base=BRIDGE_BASE).category == CATEGORY_NETWORK


def test_a_response_attribute_is_enough_for_the_status() -> None:
    class _WrappedError(Exception):
        def __init__(self, response) -> None:
            super().__init__("boom")
            self.response = response

    class _Response:
        status_code = 401

    assert (
        classify_exception(_WrappedError(_Response()), api_base=BRIDGE_BASE).category
        == CATEGORY_AUTH
    )


# ── Il provider usa la diagnosi al posto del payload grezzo ────────────────


def test_a_provider_error_becomes_the_friendly_text() -> None:
    response = OpenAICompatProvider._handle_error(_HttpError(401), api_base=BRIDGE_BASE)

    assert response.finish_reason == "error"
    assert "Токен відхилено" in response.content
    assert "upstream said no" not in response.content
    # La diagnostica non tocca i metadati: la retry policy vede ancora lo status.
    assert response.error_status_code == 401


def test_a_network_failure_on_a_bridge_carries_the_vpn_hint() -> None:
    response = OpenAICompatProvider._handle_error(ConnectError(), api_base=BRIDGE_BASE)

    assert TAILSCALE_HINT in response.content
    assert response.finish_reason == "error"


def test_an_unclassified_error_keeps_the_original_message() -> None:
    response = OpenAICompatProvider._handle_error(
        _HttpError(400, body="model not found"), api_base=BRIDGE_BASE
    )

    assert "model not found" in response.content


def test_a_public_provider_keeps_the_server_message() -> None:
    # Su un endpoint pubblico il messaggio del server (status, URL, corpo) è la
    # diagnosi migliore: riscriverlo toglierebbe informazione senza darne.
    response = OpenAICompatProvider._handle_error(
        _HttpError(401, body="invalid_api_key"), api_base="https://api.example.com/v1"
    )

    assert "invalid_api_key" in response.content
    assert diagnose_for_user(
        _HttpError(401), api_base="https://api.example.com/v1"
    ) is None


def test_without_an_api_base_nothing_is_rewritten() -> None:
    # Il ramo statico è usato dai test e dai chiamanti che non hanno un
    # endpoint: lì il messaggio originale resta, com'era prima.
    response = OpenAICompatProvider._handle_error(_HttpError(401))

    assert "upstream said no" in response.content
