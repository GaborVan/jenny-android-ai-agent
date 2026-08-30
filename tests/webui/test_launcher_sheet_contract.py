"""Le invarianti della riga del cassetto, lette sul sorgente.

Compagno di ``test_launcher_rank_client.py``, che prova il motore sotto node.
Qui si guardano le tre proprietà del *foglio* che il passo 3 esiste per
ottenere, e che si perderebbero senza rumore:

* **difetto 02** — la ``description`` che il gateway manda per ogni skill e
  ogni Jenny App arriva davvero alla riga, invece di essere buttata per un
  nome troncato;
* **difetto 05** — un guasto compare *nella* riga e su una riga sola, non in un
  blocco che alza la cella (nella griglia di oggi l'errore porta la riga da 100
  a 147 px);
* **difetto 07** — digitare **non** ricostruisce l'elenco. È il difetto più
  facile da reintrodurre: basta chiamare ``_render()`` invece di
  ``_renderList()`` nell'ascoltatore del campo, e non si nota finché non si
  prova su un telefono con 68 voci e 47 icone base64.

Asserzioni sul sorgente, nello stile del resto di ``tests/webui/``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "jenny" / "templates" / "ui" / "assets"


def _src(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = re.search(rf"\n  (?:async )?{name}\([^)]*\)\s*\{{(.*?)\n  \}}", source, re.S)
    assert body, f"{name} non trovato"
    return body.group(1)


# ── difetto 02 — la descrizione arriva alla riga ────────────────────────────

def test_the_gateway_description_reaches_the_entry() -> None:
    body = _method(_src("mobile-apps.js"), "launcherEntries")
    assert "app.description" in body, "la description delle Jenny App si perde"
    assert "_skillUserSummary(skill)" in body, (
        "le skill hanno description e user_summary: entrambi passano da qui"
    )
    assert "app.packageName" in body, (
        "le app Android non hanno descrizione: al suo posto va il pacchetto, "
        "che è un dato vero — non un testo inventato"
    )


def test_the_row_prints_the_secondary_line() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "launcher-row-desc" in body
    assert "entry.problem || entry.description" in body, (
        "un guasto ha la precedenza sulla descrizione: è quello che spiega cosa fare"
    )


def test_the_secondary_line_is_searchable() -> None:
    """3.1: si cerca su nome **e** descrizione. Se `searchText` non arrivasse
    alla voce, la ricerca tornerebbe a essere solo sui nomi senza fallire."""
    body = _method(_src("mobile-apps.js"), "launcherEntries")
    assert body.count("searchText:") == 3, "una delle tre categorie non è cercabile"


# ── difetto 05 — il guasto non deforma la riga ──────────────────────────────

def test_the_error_line_stays_on_one_line() -> None:
    css = _src("mobile-style.css")
    desc = re.search(r"\.launcher-row-desc \{(.*?)\}", css, re.S)
    assert desc, ".launcher-row-desc non trovata"
    assert "white-space: nowrap" in desc.group(1), (
        "senza nowrap un errore lungo va a capo e alza la riga: è il difetto 05"
    )
    assert "text-overflow: ellipsis" in desc.group(1)
    assert ".launcher-row-desc--problem" in css, "il guasto non ha un colore proprio"


def test_the_error_is_rendered_as_text_not_html() -> None:
    """I nomi e gli errori arrivano da manifest scritti da un LLM e dal
    PackageManager: la riga si costruisce nel DOM, mai per concatenazione."""
    source = _src("mobile-launcher.js")
    assert "innerHTML" not in source, "il foglio non deve mai scrivere HTML grezzo"
    body = _method(source, "_buildRow")
    assert body.count("textContent") >= 3


def test_the_icon_src_accepts_only_data_images() -> None:
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "entry.icon.startsWith('data:image/')" in body, (
        "`src` accetta anche schemi che eseguono, e questo valore viene da fuori"
    )


# ── difetto 07 — digitare non ricostruisce l'elenco ─────────────────────────

def test_typing_reorders_and_never_rebuilds() -> None:
    source = _src("mobile-launcher.js")
    typing = _method(source, "_onQueryChanged")
    assert "_renderList()" in typing
    assert "_render()" not in typing, (
        "il percorso del tasto non deve passare da _render(): rileggerebbe le "
        "liste e ricostruirebbe le righe a ogni carattere (difetto 07)"
    )
    assert "_syncEntries" not in typing


def test_rows_are_cached_by_key_and_reused() -> None:
    source = _src("mobile-launcher.js")
    sync = _method(source, "_syncEntries")
    assert "cached.sig === sig" in sync, (
        "senza il confronto della firma ogni apps_list_changed butterebbe via "
        "le icone base64 già decodificate per riscriverle identiche"
    )
    render = _method(source, "_renderList")
    assert "this._rows.get(entry.key).el" in render, (
        "la lista deve rimettere in fila i nodi esistenti, non crearne di nuovi"
    )


def test_the_list_is_written_once_per_keystroke() -> None:
    render = _method(_src("mobile-launcher.js"), "_renderList")
    assert render.count("this.list.replaceChildren") == 3, (
        "una sola scrittura per ramo (caricamento, nessun risultato, risultati)"
    )
    assert "appendChild" not in render, "appendere una riga per volta è N reflow"


# ── il tocco ────────────────────────────────────────────────────────────────

def test_activation_is_delegated_to_one_listener_on_the_list() -> None:
    """Le righe si rimettono in fila a ogni tasto: un listener per riga li
    moltiplicherebbe per il numero di ricostruzioni."""
    source = _src("mobile-launcher.js")
    row = _method(source, "_buildRow")
    assert "addEventListener" not in row
    assert "this.list?.addEventListener('click'" in source


def test_the_launch_policy_lives_with_the_data_owner() -> None:
    """"Aprire" significa tre cose diverse nei tre spazi di nomi, e sono già
    decise nella scheda: una seconda copia divergerebbe al primo caso
    particolare (una skill locked, una Jenny App rotta)."""
    apps = _src("mobile-apps.js")
    body = _method(apps, "activateEntry")
    assert "this.launchAndroidApp(entry.id)" in body
    assert "this.openApp(entry.id)" in body
    assert "showSkillSheet" in body and "_openSkillFile" in body
    launcher = _method(_src("mobile-launcher.js"), "_activate")
    assert "activateEntry(entry)" in launcher
    assert "api." not in launcher, "il foglio non parla con la rete (D5)"


def test_only_an_android_launch_closes_the_sheet() -> None:
    """Una app Android porta via il task; una Jenny App si apre *sopra* il
    foglio e Indietro ci riporta (1.7)."""
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert "if (entry.kind === 'android') this.close();" in body


def test_usage_is_recorded_before_the_launch() -> None:
    """``launchAndroidApp`` porta via il task: dopo di lei non è detto che
    questo JS giri ancora."""
    body = _method(_src("mobile-launcher.js"), "_activate")
    assert body.index("this._usage.record(") < body.index("activateEntry(entry)")


# ── 4.5 — la semantica, dalla nascita ───────────────────────────────────────

def test_rows_are_options_of_a_listbox_not_buttons() -> None:
    """Le celle della scheda sono `<div>` a cui `wireEvents` appiccica
    `tabindex`/`role` a ogni ridisegno, ed è un rattoppo. Qui la riga nasce
    `option` di un `listbox`: `role="button"` su ogni riga non avrebbe modo di
    esprimere *quale* è selezionata, e chi legge lo schermo sentirebbe settanta
    pulsanti uguali con l'evidenziazione ridotta a un colore."""
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "setAttribute('role', 'option')" in body
    assert "setAttribute('role', 'button')" not in body
    assert "setAttribute('aria-selected', 'false')" in body
    # Tutte focalizzabili, non solo la selezionata: su Android il gesto di
    # scorrimento di TalkBack passa per gli elementi focalizzabili, e un
    # `roving tabindex` darebbe a Tab una sola fermata su tutta la lista.
    assert "setAttribute('tabindex', '0')" in body
    html = (ROOT / "jenny" / "templates" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'role="listbox"' in html, "la lista non si dichiara"
    assert 'role="combobox"' in html, "il campo non governa la lista"


def test_the_selection_is_announced_without_moving_focus() -> None:
    """Il cuore del passo 4: le frecce muovono la selezione e il fuoco resta
    nel campo — è quello che tiene il campo scrivibile mentre si sceglie. Chi
    legge lo schermo lo sa solo grazie ad `aria-activedescendant`; senza,
    l'unica traccia della selezione sarebbe un bordo colorato."""
    body = _method(_src("mobile-launcher.js"), "_select")
    assert "aria-activedescendant" in body
    assert "aria-selected" in body
    assert ".focus(" not in body, (
        "spostare il fuoco a ogni freccia chiuderebbe la tastiera software e "
        "porterebbe via il cursore dal campo"
    )


def test_the_selected_row_is_scrolled_into_view_without_a_jump() -> None:
    """4.3: `block: 'nearest'` scorre quel tanto che basta. Con 'center' ogni
    passo sposterebbe la lista di mezza schermata."""
    body = _method(_src("mobile-launcher.js"), "_select")
    assert "scrollIntoView({ block: 'nearest' })" in body


def test_a_row_that_takes_focus_becomes_the_selection() -> None:
    """Chi arriva su una riga con Tab o col dito di TalkBack e preme ⏎ deve
    aprire *quella*, non quella evidenziata da una freccia di prima."""
    source = _src("mobile-launcher.js")
    assert "this.list?.addEventListener('focusin'" in source


# ── 4.1 — il type-ahead è quello già tarato, non una seconda copia ──────────

def test_the_type_ahead_guard_has_exactly_one_copy() -> None:
    """Le quattro condizioni erano tarate su questo hardware dentro
    `_maybeTypeAheadFocus`. Riscriverle nel cassetto avrebbe prodotto una
    seconda versione destinata a divergere sul caso raro — che qui è il caso
    vero: il keydown con `key` undefined delle tastiere fisiche."""
    guard = (ASSETS / "shared" / "type-ahead.js").read_text(encoding="utf-8")
    assert "e.key.length !== 1" in guard
    assert "e.key === ' '" in guard
    assert "e.metaKey || e.ctrlKey || e.altKey" in guard
    for name in ("mobile-chat.js", "mobile-launcher.js"):
        source = _src(name)
        assert "isTypeAheadKey" in source, f"{name} non usa la guardia condivisa"
        assert "e.key.length !== 1" not in source, (
            f"{name} si è riscritto la guardia invece di importarla"
        )


def test_the_sheet_yields_the_keys_to_whatever_is_above_it() -> None:
    """Il foglio resta aperto sotto una mini-app o sotto la scheda di una skill
    (1.7, 3.7): senza la guardia continuerebbe a rispondere a frecce e ⏎ da
    dietro un overlay. È il difetto di 1.9 rovesciato."""
    body = _method(_src("mobile-launcher.js"), "_ownsKeys")
    assert "hasOverlayAbove?.('launcher')" in body, (
        "senza il nome del livello il foglio si escluderebbe da solo: "
        "`present()` del proprio livello è vero mentre è a schermo"
    )
    app = _method(_src("mobile-app.js"), "hasOverlayAbove")
    assert "belowLayer" in app


# ── 4.4 — ⏎, ⇧⏎, Esc ────────────────────────────────────────────────────────

def test_shift_enter_opens_the_card_and_does_not_count_as_a_launch() -> None:
    """La scheda è dove si va per disinstallare o per capire cosa sia una voce:
    contarla farebbe salire in classifica proprio quelle di cui si dubita."""
    body = _method(_src("mobile-launcher.js"), "_activateSelected")
    assert "detailEntry(entry)" in body
    assert "_usage.record" not in body
    detail = _method(_src("mobile-apps.js"), "detailEntry")
    assert "showAndroidAppSheet" in detail
    assert "showJennyAppSheet" in detail
    assert "showSkillSheet" in detail


def test_escape_and_back_clear_the_field_before_closing() -> None:
    """Esc non ha un handler proprio: `keyboard.register('escape')` lo manda in
    `handleHardwareBack()`, cioè nella catena dei livelli. Una decisione sola
    per due tasti che sul Titan 2 stanno entrambi sotto le dita."""
    body = _method(_src("mobile-launcher.js"), "dismiss")
    assert "this.search.value = ''" in body
    assert body.index("this.search.value = ''") < body.index("this.close()")


def test_home_dismounts_the_sheet_in_one_call() -> None:
    """Da quando `dismiss` fa due passi, il default `layer.close ||
    layer.dismiss` non basta più: Home smonta e basta, e senza un `close`
    proprio il conto di 1.8 passerebbe da una chiamata a due."""
    layers = _method(_src("mobile-app.js"), "_overlayLayers")
    launcher = layers[layers.index("name: 'launcher'"):]
    assert "close: () => { this.launcher.close(); }" in launcher
    collapse = _method(_src("mobile-launcher.js"), "collapseToRoot")
    assert "this.close()" in collapse


# ── tre difetti visti girare sull'emulatore, e le loro guardie ──────────────

def test_reopening_the_sheet_starts_from_the_top_row() -> None:
    """Senza questo, ⏎ appena aperto lanciava la riga evidenziata l'altra volta
    — cioè avviava qualcosa che nessuno aveva scelto adesso. Osservato: aperto
    il foglio, la selezione era ancora su "Google" della sessione precedente."""
    body = _method(_src("mobile-launcher.js"), "open")
    assert "this._select(null)" in body, (
        "azzerare solo `_selectedKey` lascerebbe la riga di ieri marcata: la "
        "sua classe e il suo aria-selected stanno sul nodo, non nella chiave"
    )
    assert "this._selectionPinned = false" in body


def test_the_selection_follows_the_top_until_someone_moves_it() -> None:
    """Le app Android arrivano dopo le skill: una selezione *pinnata* fin dal
    primo disegno resta incollata a chi era in cima quando la lista era ancora
    a metà, e si finisce con la dodicesima riga evidenziata, fuori schermo.
    Osservato: `selected: app-creator` con `first: Camera`."""
    render = _method(_src("mobile-launcher.js"), "_renderList")
    assert "this._selectionPinned && this._rankedKeys.includes" in render
    typing = _method(_src("mobile-launcher.js"), "_onQueryChanged")
    assert "_selectionPinned = false" in typing, (
        "una query nuova è una domanda nuova: la risposta migliore torna in cima"
    )
    move = _method(_src("mobile-launcher.js"), "_moveSelection")
    assert "_selectionPinned = true" in move


def test_the_decorative_glyph_is_not_read_aloud() -> None:
    """I glifi Tabler sono caratteri della zona a uso privato dentro un font:
    senza `aria-hidden` il nome accessibile della riga di una skill comincia
    con un carattere di spazzatura — letto nell'albero di accessibilità della
    WebView, non dedotto. L'icona di una app Android non ha il problema: è una
    `<img alt="">`, che al nome non contribuisce."""
    body = _method(_src("mobile-launcher.js"), "_buildRow")
    assert "glyph.setAttribute('aria-hidden', 'true')" in body
    # Il glifo del server invece *porta* informazione: non si nasconde, si nomina.
    assert "server.setAttribute('aria-label'" in body


# ── la rotella ──────────────────────────────────────────────────────────────

def test_the_wheel_moves_the_selection_and_says_what_is_unverified() -> None:
    """Quali eventi produca la rotella del Titan 2 **non è accertato**, e
    sull'emulatore la rotella non c'è. Le due letture più probabili sono
    coperte entrambe (`wheel` qui, ↑↓ in `_onKeyDown`); ciò che non si può
    dire da qui va scritto dove chi legge il codice lo trova."""
    source = _src("mobile-launcher.js")
    body = _method(source, "_onWheel")
    assert "_moveSelection" in body
    assert "e.preventDefault()" in body
    assert "deltaMode" in body, (
        "una rotella che conta righe e una che conta pixel non si sommano"
    )
    assert "{ passive: false }" in source, (
        "senza passive:false il preventDefault non ferma lo scorrimento"
    )
    assert "non è accertato" in source, "l'incognita della rotella non è dichiarata"


# ── i18n ────────────────────────────────────────────────────────────────────

def test_no_hardcoded_strings_in_the_sheet() -> None:
    source = _src("mobile-launcher.js")
    for key in ("launcher.recent", "launcher.results", "launcher.noResults"):
        assert f"'{key}'" in source, f"{key} non usata"
    # Il placeholder e le etichette statiche stanno nell'HTML, non nel JS.
    html = (ROOT / "jenny" / "templates" / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'data-i18n-placeholder="launcher.searchPlaceholder"' in html
    assert 'data-i18n-aria="launcher.clearSearch"' in html


def test_the_search_field_does_not_autofocus() -> None:
    """D6: su un telefono con tastiera software l'autofocus alzerebbe la
    tastiera e si mangerebbe il foglio."""
    html = (ROOT / "jenny" / "templates" / "ui" / "index.html").read_text(encoding="utf-8")
    field = re.search(r'<input class="launcher-search".*?>', html, re.S)
    assert field, "campo di ricerca non trovato"
    assert "autofocus" not in field.group(0)
    open_body = _method(_src("mobile-launcher.js"), "open")
    assert "this.search.focus" not in open_body
