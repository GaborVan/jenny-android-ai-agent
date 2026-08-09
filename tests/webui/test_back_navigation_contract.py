"""Il tasto Indietro di Android: una pressione, un cambiamento visibile.

Il guscio nativo inoltra *sempre* il back alla SPA (``OnBackPressedCallback(true)``
in ``MainActivity``), quindi qui non c'è niente che arrivi gratis: né la chiusura
dei ``<dialog>``, né la risalita dentro una sezione. Prima la SPA conosceva solo i
tab, e tutto il resto (dialog, drawer, cartelle del workspace, step
dell'onboarding) non esisteva per la history: il back scavalcava il livello in cui
l'utente si trovava. In più due sorgenti impilavano entry gemelle — il boot
(``replaceState`` + ``switchMode`` che pusha) e ``api.reload()`` (assegnare
``location.hash`` *è* una navigazione) — e su quelle la pressione veniva ingoiata
in silenzio: da lì la sensazione che il tasto "saltasse" le pagine.

Il contratto è: catena di consumatori dall'alto verso il basso, un unico punto di
scrittura della history, nessuna entry gemella.

Asserzioni sul sorgente, nello stile di ``test_thinking_scroll_contract.py``: la
WebUI non ha un runner JS con DOM.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"
APP_JS = ASSETS / "mobile-app.js"
MAIN_ACTIVITY = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "flagdizero" / "jenny" / "MainActivity.kt"


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  {name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


def test_the_shell_hands_every_back_press_to_the_spa() -> None:
    """Il presupposto della catena: il nativo non ne gestisce nessun caso da sé."""
    kotlin = MAIN_ACTIVITY.read_text(encoding="utf-8")
    assert "OnBackPressedCallback(true)" in kotlin
    assert "handleHardwareBack()" in kotlin


def test_back_unwinds_the_layers_top_down() -> None:
    """L'ordine è quello di sovrapposizione: chi sta sopra si chiude per primo.

    Un livello fuori posto è invisibile nel codice ma non a schermo: chiudere
    (per dire) il drawer mentre una modale copre tutto lascia l'utente davanti
    alla stessa immagine, e la pressione sembra persa.
    """
    body = _method(_app(), "handleHardwareBack")
    layers = [
        "dialog[open]",                              # top layer
        ".image-lightbox",                           # overlay immagini
        "this.controllers.apps?.handleBack()",       # mini-app
        "this.jenny?.handleBack()",                  # minichat della mascotte
        "this.drawer.activeDrawer",                  # drawer
        "this.controllers[this.currentMode]?.handleBack?.()",  # sotto-stato di sezione
        "window.history.back()",                     # schermata precedente
    ]
    positions = []
    for marker in layers:
        assert marker in body, f"livello mancante nella catena del back: {marker}"
        positions.append(body.index(marker))
    assert positions == sorted(positions), "i livelli del back non sono in ordine di sovrapposizione"


def test_closing_a_dialog_goes_through_a_cancelable_event() -> None:
    """Chiamare ``close()`` scavalcherebbe chi rifiuta la chiusura.

    ``showRestartDialog`` (backup-flow) fa ``preventDefault()`` sul ``cancel``
    apposta: dopo un restore il riavvio non è opzionale. Il back deve avere la
    stessa semantica di Esc, non un potere in più.
    """
    body = _method(_app(), "handleHardwareBack")
    assert "new Event('cancel', { cancelable: true })" in body
    assert ".close()" in body, "chi non rifiuta va comunque chiuso"

    backup = (ASSETS / "shared" / "backup-flow.js").read_text(encoding="utf-8")
    assert "addEventListener('cancel', (e) => e.preventDefault())" in backup, (
        "sparito il dialog non annullabile: il giro via evento `cancel` non ha più motivo di esistere"
    )


def test_back_at_the_root_does_nothing_and_never_trusts_history_length() -> None:
    """Jenny è il launcher: sotto la radice non c'è nessuna app a cui tornare.

    ``history.length`` non sa rispondere: conta l'intera sessione del WebView
    (iframe delle mini-app, reload) e non cala mai.
    """
    body = _method(_app(), "handleHardwareBack")
    assert "this._navPos > 0" in body, "il fondo dello stack va riconosciuto dalla posizione nostra"
    assert "history.length" not in body


def test_every_history_write_goes_through_the_single_funnel() -> None:
    """Un solo punto di scrittura, altrimenti le entry tornano ad accumularsi."""
    for js in ASSETS.rglob("*.js"):
        if js.name in {"jenny-sdk.js", "api-client.js"} or js == APP_JS:
            continue  # sdk: gira dentro l'iframe, history propria; api-client: v. test dedicato
        source = js.read_text(encoding="utf-8")
        assert "history.pushState" not in source, f"{js.name} scrive la history fuori da pushNav"
        assert "history.replaceState" not in source, f"{js.name} scrive la history fuori da replaceNav"


def test_the_funnel_stamps_the_stack_position_and_refuses_twins() -> None:
    source = _app()
    push = _method(source, "pushNav")
    assert "pos: this._navPos" in push, "senza posizione il back non sa dov'è il fondo"
    assert "this.replaceNav(state)" in push, (
        "impilare due volte la stessa schermata regala una pressione di Indietro che non cambia niente"
    )
    assert "pos: this._navPos" in _method(source, "replaceNav")


def test_the_boot_does_not_stack_a_twin_of_the_initial_view() -> None:
    """La entry iniziale *è* la vista iniziale: si riscrive, non si impila."""
    source = _app()
    assert "this.replaceNav(this._navStateFor(initialMode, initialWiki, initialPage));" in source
    assert "this.switchMode(initialMode, false);" in source, (
        "switchMode(initialMode) con push impilerebbe una gemella della radice"
    )


def test_the_reload_leaves_no_ghost_entry() -> None:
    """``location.hash = ...`` è una navigazione: un fantasma per ogni reload."""
    source = (ASSETS / "shared" / "api-client.js").read_text(encoding="utf-8")
    reload_body = re.search(r"\n  reload\(\)\s*\{(.*?)\n  \}", source, re.S)
    assert reload_body, "reload() non trovato"
    code = re.sub(r"//.*", "", reload_body.group(1))  # il commento cita ciò che non si fa
    assert "location.hash =" not in code
    assert "history.replaceState" in code


def test_sections_with_their_own_depth_expose_a_back_handler() -> None:
    """Cartelle ed editor del workspace, step dell'onboarding: risalire dentro
    la sezione viene prima di uscirne."""
    workspace = (ASSETS / "mobile-workspace.js").read_text(encoding="utf-8")
    ws_back = _method(workspace, "handleBack")
    assert "parentPath(this.currentDir)" in ws_back, "il back deve risalire di una cartella"
    assert "this.viewMode === 'editor'" in ws_back
    assert "return !ret;" in ws_back, (
        "l'editor aperto da un'altra sezione lascia proseguire il back: quella entry è già nello stack"
    )

    onboarding = (ASSETS / "mobile-onboarding.js").read_text(encoding="utf-8")
    onb_back = _method(onboarding, "handleBack")
    assert "_goToStep0()" in onb_back and "_goBackToStep1()" in onb_back
    assert "return true;" in onb_back, "dall'onboarding non si esce col back"
