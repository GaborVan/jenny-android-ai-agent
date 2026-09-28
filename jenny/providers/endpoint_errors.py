"""Diagnosi leggibile di un endpoint provider: token, backend, rete.

Un 401 (token sbagliato), un 503 (il CLI dietro il bridge ha esaurito la
sessione) e un telefono senza Tailscale sono tre guasti diversi che fino a ora
arrivavano all'utente come lo stesso messaggio grezzo
(``HTTP 503 from https://…: {"error": …}``). Qui la distinzione è una funzione
pura su status e tipo di errore, e il testo che ne esce è corto, in ucraino,
senza stack né URL.

La regola che ripaga da sola: un guasto **di rete** (o un timeout) verso un host
``*.ts.net`` è quasi sempre il VPN spento sul telefono, non il bridge rotto — e
distinguere i due costa mezz'ora di diagnosi a chi non lo sa.

Modulo foglia: stdlib pura, nessuna I/O. Chi chiama decide *quando* diagnosticare
(i provider lo fanno solo per gli errori che finiscono davanti all'utente).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "CATEGORY_AUTH",
    "CATEGORY_BACKEND",
    "CATEGORY_NETWORK",
    "CATEGORY_TIMEOUT",
    "CATEGORY_UNKNOWN",
    "TAILSCALE_HINT",
    "TAILSCALE_SUFFIX",
    "EndpointDiagnosis",
    "classify_endpoint_error",
    "classify_exception",
    "diagnose_for_user",
    "is_tailscale_host",
]

#: Suffisso MagicDNS di Tailscale. Un host che lo porta è raggiungibile solo
#: dentro il tailnet: se la richiesta non esce, il sospetto è il VPN, non il server.
TAILSCALE_SUFFIX = ".ts.net"

#: Il testo che l'utente deve vedere quando il sospetto è Tailscale. È in ucraino
#: perché è la lingua di questo dispositivo, non una scelta di localizzazione
#: generale: le stringhe della WebUI restano in ``templates/ui/assets/i18n``.
TAILSCALE_HINT = (
    "Схоже, Tailscale вимкнено або недоступний. Увімкніть VPN-з'єднання "
    "Tailscale на телефоні (і переконайтеся, що MagicDNS увімкнений)."
)

# Categorie stabili: chi consuma la diagnosi decide sul codice, non sul testo.
CATEGORY_AUTH = "auth"
CATEGORY_BACKEND = "backend"
CATEGORY_NETWORK = "network"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_UNKNOWN = "unknown"


@dataclass(frozen=True)
class EndpointDiagnosis:
    """Un guasto classificato, con il testo già pronto per l'utente."""

    category: str
    message: str
    hint: str | None = None
    status_code: int | None = None

    def text(self) -> str:
        """Messaggio e suggerimento in una riga sola, come si mostra in chat."""
        return f"{self.message} {self.hint}" if self.hint else self.message


def is_tailscale_host(api_base: str | None) -> bool:
    """True se l'host di *api_base* è un nome MagicDNS ``*.ts.net``.

    Un URL senza schema (``host:porta``) viene letto lo stesso: la config accetta
    entrambe le forme, e ``urlparse`` da solo scambierebbe l'host per schema.
    Un host illeggibile vale ``False`` — meglio nessuna diagnosi che una
    diagnosi sbagliata.
    """
    if not api_base:
        return False
    raw = api_base.strip().lower()
    if not raw:
        return False
    try:
        host = urlparse(raw if "://" in raw else f"//{raw}").hostname
    except ValueError:
        return False
    return bool(host) and host.endswith(TAILSCALE_SUFFIX)


def classify_endpoint_error(
    *,
    status_code: int | None = None,
    error_kind: str | None = None,
    api_base: str | None = None,
) -> EndpointDiagnosis:
    """Classifica un guasto di chiamata al modello in una diagnosi mostrabile.

    L'ordine delle regole conta: uno **status** dice che una risposta è arrivata,
    quindi vince sempre sulla forma dell'eccezione. Un 502/503 su un host
    Tailscale è il backend dietro il bridge che non risponde — la rete ha
    funzionato, e mandare l'utente a controllare il VPN sarebbe una diagnosi
    sbagliata. Solo l'assenza di status (nessuna risposta) fa scattare il
    sospetto di rete, e il suggerimento Tailscale.

    ``error_kind`` (``"timeout"``/``"connection"``) separa i due casi di rete
    nel testo; senza di esso, nessuna risposta vale comunque "rete".
    """
    status = _coerce_status(status_code)
    if status is not None:
        if status in (401, 403):
            return EndpointDiagnosis(
                CATEGORY_AUTH,
                f"Токен відхилено (HTTP {status}). Перевірте токен у налаштуваннях.",
                hint=(
                    "Значення має збігатися з токеном моста: у полі токена "
                    "потрібен саме він, а не API-ключ стороннього сервісу."
                ),
                status_code=status,
            )
        if 500 <= status < 600:
            return EndpointDiagnosis(
                CATEGORY_BACKEND,
                f"Бекенд моделі недоступний (HTTP {status}).",
                hint=(
                    "Міст відповідає, але модель за ним не готова — найчастіше "
                    "вичерпано ліміт сесії CLI. Спробуйте пізніше або іншу "
                    "модель (codex/claude/gemini)."
                ),
                status_code=status,
            )
        return EndpointDiagnosis(
            CATEGORY_UNKNOWN,
            f"Запит до провайдера не вдався (HTTP {status}).",
            status_code=status,
        )

    # Nessuno status: la risposta non è mai arrivata.
    tailscale = is_tailscale_host(api_base)
    if error_kind == "timeout":
        return EndpointDiagnosis(
            CATEGORY_TIMEOUT,
            "Домашній міст не відповів вчасно."
            if tailscale
            else "Провайдер не відповів вчасно.",
            hint=TAILSCALE_HINT if tailscale else None,
        )
    return EndpointDiagnosis(
        CATEGORY_NETWORK,
        "Не вдалося з'єднатися з домашнім мостом."
        if tailscale
        else "Не вдалося з'єднатися з провайдером.",
        hint=TAILSCALE_HINT if tailscale else None,
    )


def classify_exception(
    exc: BaseException,
    *,
    api_base: str | None = None,
) -> EndpointDiagnosis:
    """Classifica un'eccezione di trasporto leggendone status e tipo.

    Status e ``error_kind`` stanno in posti diversi a seconda che l'errore
    venga da httpx, dall'SDK Anthropic o da una nostra ``ProviderHTTPError``;
    ``getattr`` su tutti e tre evita di importare l'uno o l'altro qui — e
    questo modulo resta una foglia.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
    return classify_endpoint_error(
        status_code=_coerce_status(status),
        error_kind=_error_kind(exc),
        api_base=api_base,
    )


def diagnose_for_user(
    exc: BaseException,
    *,
    api_base: str | None = None,
) -> EndpointDiagnosis | None:
    """La diagnosi da sostituire al messaggio grezzo, o ``None`` se non serve.

    Solo un endpoint ``.ts.net`` viene riscritto. Per un provider pubblico il
    messaggio del server — status, URL, corpo — *è* la diagnosi migliore
    disponibile, e rimpiazzarlo con una frase nostra toglierebbe informazione
    (c'è un test che lo pretende, ``test_the_message_still_names_status_and_url``).
    Per il bridge di casa vale il contrario: l'utente sa già dove ha puntato, e
    quello che non può sapere è se il guasto è il suo VPN.

    Anche qui un caso non riconosciuto non si riscrive: ``None`` lascia al
    chiamante il messaggio originale.
    """
    if not is_tailscale_host(api_base):
        return None
    diagnosis = classify_exception(exc, api_base=api_base)
    if diagnosis.category == CATEGORY_UNKNOWN:
        return None
    return diagnosis


def _coerce_status(status: object) -> int | None:
    """Uno status intero, o ``None``: un valore strano non deve classificare nulla."""
    if status is None or isinstance(status, bool):
        return None
    try:
        return int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _error_kind(exc: BaseException) -> str | None:
    """``"timeout"``/``"connection"`` dal nome della classe di eccezione.

    È il criterio che i provider usano già per ``error_kind`` (``httpx`` e gli
    SDK battezzano le classi ``…Timeout``/``…ConnectError``), e leggerlo dal
    nome evita di dipendere dai loro tipi.
    """
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connect" in name or "connection" in name:
        return "connection"
    return None
