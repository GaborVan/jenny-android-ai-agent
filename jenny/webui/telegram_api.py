"""Helper REST per la configurazione del canale Telegram dalla WebUI.

Stesso confine di ``settings_api``: questo modulo possiede la forma dei
payload e le mutazioni di config consentite (token, pairing, unpair,
disable). Il token non è mai restituito in chiaro, solo come hint.
"""

from __future__ import annotations

import secrets
from typing import Any

from loguru import logger

from jenny.config import store
from jenny.config.loader import load_config
from jenny.config.schema import Config
from jenny.webui.settings_api import WebUISettingsError


def _token_hint(token: str | None) -> str:
    if token and len(token) > 8:
        return token[:4] + "..." + token[-4:]
    return ""


def _new_pairing_code() -> str:
    """Codice di pairing a 6 cifre (mostrato in WebUI, inviato al bot)."""
    return f"{secrets.randbelow(1_000_000):06d}"


# Menu comandi del bot (setMyCommands), localizzato come le _BOT_STRINGS del
# canale. Descrizioni 1-256 caratteri per vincolo Telegram.
_BOT_COMMANDS: dict[str, list[dict[str, str]]] = {
    "it": [
        {"command": "start", "description": "Guida rapida"},
        {"command": "new", "description": "Nuova conversazione"},
    ],
    "en": [
        {"command": "start", "description": "Quick guide"},
        {"command": "new", "description": "New conversation"},
    ],
}


def _bot_commands(language: str) -> list[dict[str, str]]:
    return _BOT_COMMANDS.get(language, _BOT_COMMANDS["en"])


def telegram_status_payload(config: Any = None) -> dict[str, Any]:
    """Stato corrente del canale per la UI (polling durante il pairing)."""
    if config is None:
        config = load_config()
    tg = config.telegram
    return {
        "enabled": tg.enabled,
        "configured": bool(tg.bot_token),
        "token_hint": _token_hint(tg.bot_token),
        "bot_username": tg.bot_username,
        "paired": bool(tg.paired_chat_id),
        "paired_username": tg.paired_username,
        # Il codice serve alla UI solo finché non c'è una chat accoppiata.
        "pairing_code": tg.pairing_code if not tg.paired_chat_id else None,
    }


async def save_telegram_token(token: str) -> dict[str, Any]:
    """Valida il token con ``getMe``, abilita il canale e genera il codice.

    Fallisce forte (come la validazione provider dell'onboarding) così un
    token errato non lascia il canale in uno stato zombie.
    """
    from jenny.channels.telegram_api import TelegramAPI, TelegramAPIError

    token = token.strip()
    if not token:
        raise WebUISettingsError("telegram token is required")

    # Le tre chiamate di rete stanno DELIBERATAMENTE prima di entrare nel
    # funnel: qui la config si legge solo per la lingua del menu comandi, e
    # tenerla in mano attraverso secondi di rete era il caso reale di lost
    # update (una qualsiasi impostazione salvata nel frattempo veniva
    # riportata indietro da questo handler).
    language = load_config().agents.defaults.language
    api = TelegramAPI(token)
    try:
        me = await api.get_me()
        try:
            # Menu comandi (/start, /new): best-effort, un fallimento qui non
            # deve mai far fallire il salvataggio del token.
            await api.set_my_commands(_bot_commands(language))
        except Exception:
            logger.warning("Telegram setMyCommands failed (non-fatal)")
    except TelegramAPIError as e:
        raise WebUISettingsError(f"telegram token rejected: {e.description}") from e
    except Exception as e:
        raise WebUISettingsError(
            f"cannot reach Telegram: {type(e).__name__}", status=502
        ) from e
    finally:
        await api.close()

    username = me.get("username")

    def _apply(config: Config) -> None:
        tg = config.telegram
        tg.bot_token = token
        tg.enabled = True
        tg.bot_username = username if isinstance(username, str) else None
        # Nuovo token ⇒ nuovo pairing: qualunque accoppiamento precedente decade.
        tg.paired_chat_id = None
        tg.paired_username = None
        tg.pairing_code = _new_pairing_code()

    config = await store.mutate(_apply)
    logger.info("Telegram token saved, pairing code generated")
    return telegram_status_payload(config)


async def unpair_telegram() -> dict[str, Any]:
    """Scollega la chat corrente e rigenera un codice di pairing."""

    def _apply(config: Config) -> None:
        tg = config.telegram
        if not tg.bot_token:
            raise WebUISettingsError("telegram is not configured")
        tg.paired_chat_id = None
        tg.paired_username = None
        tg.pairing_code = _new_pairing_code()

    config = await store.mutate(_apply)
    logger.info("Telegram unpaired, new pairing code generated")
    return telegram_status_payload(config)


async def disable_telegram() -> dict[str, Any]:
    """Disabilita il canale conservando il token (riattivabile senza BotFather)."""

    def _apply(config: Config) -> None:
        config.telegram.enabled = False

    config = await store.mutate(_apply)
    logger.info("Telegram channel disabled")
    return telegram_status_payload(config)


async def record_paired(chat_id: str, username: str | None) -> None:
    """Persiste l'esito del pairing (callback ``on_paired`` del canale).

    Async perché passa dal funnel: il pairing arriva da un update Telegram e
    può cadere mentre l'utente sta salvando impostazioni dalla WebUI.
    """
    try:

        def _apply(config: Config) -> None:
            tg = config.telegram
            tg.paired_chat_id = chat_id
            tg.paired_username = username
            tg.pairing_code = None

        await store.mutate(_apply)
        logger.info("Telegram paired with chat {} (@{})", chat_id, username or "-")
    except Exception:
        logger.exception("Failed to persist telegram pairing")
