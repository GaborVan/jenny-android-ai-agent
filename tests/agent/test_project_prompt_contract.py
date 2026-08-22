"""Dentro un progetto il prompt dice la verità su dov'è, e tace sugli altri.

Passi **2.1** e **2.2** di ``roadmap/progetti-passi.md``. Il passo 1 ha legato la
cartella al turno, ma il prompt aveva continuato a descrivere quella cartella
come se fosse il workspace. Tre affermazioni false, tutte misurate il 21/08:

1. ``agent/identity.md`` componeva ``memory/MEMORY.md``, ``memory/history.jsonl``
   e ``skills/`` **sulla cartella del turno**: dentro un progetto erano tre
   percorsi inesistenti nelle prime dieci righe di ogni prompt. Era il resto del
   lavoro dell'1.2, che aveva sdoppiato la radice dei soli file di bootstrap.
2. ``## Where Produced Files Go`` (in ``tool_contract.md``, e parola per parola
   anche in ``subagent_system.md``) mandava quel che si produce in
   ``<radice>/output/`` e vietava di scrivere nella "radice del workspace"
   perché "contiene un insieme fisso di documenti". Dentro una wiki sono due
   cose false, ed è **da qui** che il file di prova del 21/08 è finito in
   ``wikis/zz-prova-claude/output/`` — una cartella che nello scaffold non
   esiste (c'è ``outputs/``) — con la motivazione «la radice è riservata ai file
   fissi». Non l'aveva inventata: gliel'avevamo scritta noi.
3. ``memory/WIKI.md`` — la rubrica di Atlas — elencava tutte le wiki dentro il
   prompt di ognuna, più persone, progetti e piante.

La riga di confine è: **chi sei viaggia, dove altro lavori no.**

Due domande diverse, e due guardie diverse apposta:

- *la cartella del turno è una wiki?* → decide ``agent/project.md`` e le due
  sezioni di ``tool_contract.md``. Si chiude sulla **cartella** perché la stessa
  risposta serve al subagent, che riceve la radice dal ``WorkspaceScope`` e la
  chiave di sessione non ce l'ha mai — ed è il subagent ad aver scritto in
  ``output/``;
- *questa conversazione è un progetto?* → decide la rubrica. È una domanda su
  chi sta parlando, non su dove si lavora.
"""

from __future__ import annotations

import pathlib

from jenny.agent.context import ContextBuilder

SRC = pathlib.Path(__file__).resolve().parents[2] / "jenny"

WORKSPACE_FILE_RULES = ("## Where Produced Files Go", "## Which File a Fact Belongs In")


# Radice **per test**, mai quella della suite: ``conftest`` ne monta una sola per
# tutta la sessione, e scriverci dentro un ``memory/WIKI.md`` lo fa trovare a chi
# gira dopo — successo la prima volta che questo file è stato scritto, e a
# cadere è stato un test di Atlas a tre cartelle di distanza. I *template* invece
# vengono da lì e devono continuare a venirne: ``render_template`` legge dal
# workspace configurato, non dal package.
def _wiki(root: pathlib.Path, name: str) -> pathlib.Path:
    """Una wiki è una cartella che contiene ``wiki/`` — la definizione del picker."""
    project = root / "wikis" / name
    (project / "wiki").mkdir(parents=True, exist_ok=True)
    return project


def _prompts(root: pathlib.Path) -> tuple[str, str]:
    """``(prompt di progetto, prompt personale)`` sullo stesso workspace."""
    project = _wiki(root, "etf-finance")
    builder = ContextBuilder(root)
    return (
        builder.build_system_prompt(workspace=project, session_key="project:etf-finance"),
        builder.build_system_prompt(session_key="unified:default"),
    )


# ── 2.1 — il blocco, e le tre affermazioni che diventavano false ──────────


def test_the_block_renders_inside_a_wiki_and_nowhere_else(tmp_path) -> None:
    project, personal = _prompts(tmp_path)
    assert "# Project Folder" in project
    assert "# Project Folder" not in personal, (
        "il blocco descrive una pianta che nella chat personale non esiste"
    )


def test_the_block_only_renders_for_a_folder_that_really_is_a_wiki(tmp_path) -> None:
    """Legata a una cartella senza ``wiki/`` dentro, la pianta non c'è: non si detta.

    È il caso del passo 6 (cartella sparita o mai scaffoldata) e la risposta qui
    è tacere, non descrivere cartelle che non ci sono.
    """
    root = tmp_path
    empty = root / "wikis" / "mai-scaffoldata"
    empty.mkdir(parents=True, exist_ok=True)
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=empty, session_key="project:mai-scaffoldata"
    )
    assert "# Project Folder" not in prompt


def test_the_block_stays_small() -> None:
    """Un tetto, non un'abitudine — stessa ragione di ``agent/scheduling.md``.

    Si paga a **ogni** turno del progetto, compresi quelli in cui gli chiedi che
    ore sono.

    **Il tetto è passato da 1500 a 3200 il 22/08 (T2), e la regola è cambiata
    con lui.** Prima diceva "la pianta sta qui, il come si opera sta nella
    skill": era giusta finché questo blocco era una pianta. Ora contiene anche la
    **politica di cattura**, che non è profondità e non sta in nessun manuale —
    è la regola che si incontra a ogni turno e che, se assente, produce
    esattamente la serata del 22/08: conversazione buona e zero righe su disco.

    La regola nuova: **qui ci sta quel che si applica a ogni turno; il manuale di
    un'operazione resta nella skill.** Se il prossimo che aggiunge un paragrafo
    sta descrivendo *come si fa* qualcosa che capita di rado, quel paragrafo va
    nella skill e questo tetto ha fatto il suo lavoro.
    """
    from jenny.utils.prompt_templates import render_template

    rendered = render_template("agent/project.md", project_path="/data/workspace/wikis/x")
    assert len(rendered) <= 3200, (
        f"agent/project.md è {len(rendered)} caratteri: sta diventando il manuale della "
        "skill. Qui ci sta quel che si applica a ogni turno; il come si esegue "
        "un'operazione sta in `skills/llm-wiki/SKILL.md`."
    )


# ── T2: la conversazione è una fonte ─────────────────────────────────────────


def _project_prompt(tmp_path) -> str:
    project = _wiki(tmp_path, "viaggio")
    return ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:viaggio"
    )


def test_the_capture_rule_is_in_the_block(tmp_path) -> None:
    """Il difetto del 22/08 in una riga: nessuno aveva mai detto all'agente che
    la conversazione è una fonte, quindi la regola se l'è inventata a sessione —
    e la sua versione era «finché ci muoviamo a sensazione non scrivo niente».

    Il criterio deve essere **verificabile da chi lo legge**, non un invito a
    valutare: "sarà ancora vero la settimana prossima?" si risponde, "è
    importante?" no.
    """
    prompt = _project_prompt(tmp_path)
    assert "true next" in prompt and "before you answer" in prompt
    assert "raw/journal/YYYYMMDD.md" in prompt


def test_the_gesture_is_a_line_and_not_a_page(tmp_path) -> None:
    """Il discrimine fra le due strade (P3 e P2, v. ``roadmap/progetti-taccuino.md``):
    in conversazione si **cattura**, non si scrive. Scegliere il nome e la
    cartella di una pagina a caldo è il lavoro che produce tassonomie diverse in
    sessioni diverse, e per questo è di chi passa dopo."""
    prompt = _project_prompt(tmp_path)
    # Il gesto è **una chiamata a un tool**, non un'istruzione su un file. Il
    # collaudo del 22/08 ha mostrato perché: `orchestrator_mode` toglie la
    # scrittura all'agente principale, quindi «appendi una riga» si traduceva in
    # uno spawn di subagent — una corsa intera per una riga, a ogni turno con un
    # fatto dentro. `journal_append` (T2.5) la rende una chiamata.
    assert "journal_append" in prompt
    # Frammento che non attraversa un a-capo del template: il testo è impaginato
    # a 100 colonne, e un'asserzione su una frase intera si rompe alla prima
    # riformattazione invece che al primo cambio di senso.
    assert "no subagent to spawn" in prompt
    assert "no folder to choose" in prompt


def test_it_does_not_ask_permission_to_write(tmp_path) -> None:
    """L'altra metà del 22/08: dopo aver detto che avrebbe salvato, ha chiesto
    «va bene così?» con l'interruttore già su *Writes* a due centimetri dal
    messaggio. Quell'interruttore **è** il permesso: richiederlo a parole riapre
    una domanda che l'utente ha già chiuso."""
    prompt = _project_prompt(tmp_path)
    assert "Do not ask permission to write" in prompt


def test_the_answer_cites_the_pages(tmp_path) -> None:
    """R6 come segnale visibile: una risposta che non cita niente si vede a
    occhio, e dice che il taccuino non sta lavorando. Vale poco finché le pagine
    non esistono, ed è giusto che ci sia da subito."""
    prompt = _project_prompt(tmp_path)
    assert "[[page-name]]" in prompt


def test_an_older_layout_is_followed_and_not_corrected(tmp_path) -> None:
    """Due forme esistono su disco e **nessun flag** le distingue: la struttura
    è la dichiarazione. Le sette wiki vere hanno altre cartelle, e l'agente le
    deve seguire invece di tentare una migrazione che nessuno ha chiesto."""
    prompt = _project_prompt(tmp_path)
    assert "Follow the structure" in prompt
    assert "is the authority" in prompt


def test_the_research_taxonomy_is_not_prescribed_anymore(tmp_path) -> None:
    """Il blocco non nomina più ``concepts``/``entities``/``summaries`` come la
    pianta: quella tassonomia è del pattern di ricerca, vive nella skill, e
    prescriverla qui rimetterebbe la domanda «concept o entity?» nel momento in
    cui si prende un appunto."""
    prompt = _project_prompt(tmp_path)
    block = prompt.split("# Project Folder", 1)[1].split("\n# ", 1)[0]
    for folder in ("wiki/concepts", "wiki/entities", "wiki/summaries", "outputs/queries"):
        assert folder not in block, folder


def test_the_subagent_gets_the_layout_but_not_the_capture_rule() -> None:
    """Un subagent scrive nella cartella, quindi la pianta gli serve. La cattura
    no, e non è un dettaglio di economia: **non ha un utente**. La sua materia
    prima è il prompt che gli ha scritto l'agente principale, e se catturasse,
    nel diario finirebbe il suo ragionamento intermedio — che è l'ingresso del
    giardiniere, quindi quel rumore diventerebbe pagine.

    Un file solo con una condizione, non due template: il layout è la stessa
    verità per tutti e due, e due copie divergerebbero al primo cambio.
    """
    from jenny.utils.prompt_templates import render_template

    args = {"project_path": "/w/wikis/x"}
    con = render_template("agent/project.md", capture=True, **args)
    senza = render_template("agent/project.md", capture=False, **args)

    assert "# Project Folder" in senza and "raw/journal/YYYYMMDD.md" in senza
    assert "before you answer" not in senza
    assert "Do not ask permission" not in senza
    assert "before you answer" in con
    assert len(senza) < len(con)


def test_the_two_callers_pass_the_flag_explicitly() -> None:
    """Jinja2 valuta falsa una variabile assente invece di sollevare: un errore di
    battitura nel nome spegnerebbe la cattura **in silenzio**, che è esattamente
    il difetto del 22/08 tornato per un'altra strada. Quindi i due chiamanti si
    pinnano qui: se qualcuno rinomina il flag, questo test lo dice.

    Il turno vero lo lega alla **scrivibilità** e non a `True`: in sola lettura
    la regola non si rende (v. ``test_readonly_prompt_contract``). Il subagent lo
    lega a `False` sempre: non ha un utente da cui catturare.
    """
    for module, expected in (
        ("context.py", "capture=_turn_is_writable()"),
        ("subagent.py", "capture=False"),
    ):
        src = (SRC / "agent" / module).read_text(encoding="utf-8")
        assert expected in src, f"{module}: manca {expected}"


def test_the_orchestrator_block_names_the_one_write_it_may_do() -> None:
    """Misurato sul telefono il 22/08, e per la terza volta in un giorno ha deciso
    **la posizione**: ``agent/orchestrator.md`` si rende *dopo*
    ``agent/project.md``, e dice a chiare lettere «non puoi scrivere file» e
    «delega la scrittura di file». Con `journal_append` nei 22 tool registrati e
    l'istruzione di usarlo scritta più su, l'agente ha comunque spawnato un
    subagent — che è la prosa più in basso che vince, non un capriccio.

    Quindi l'eccezione va detta **dove sta la regola**, non dove sta il desiderio:
    un solo proprietario per regola, come per le due sezioni di
    ``tool_contract.md`` che il passo 2 spegne dentro un progetto invece di
    riscriverle altrove.
    """
    text = (SRC / "templates" / "agent" / "orchestrator.md").read_text(encoding="utf-8")
    assert "journal_append" in text
    # E la regola generale resta: l'eccezione è una riga, non una porta aperta.
    assert "still a spawn" in text


# ── T3: la mappa entra d'ufficio ─────────────────────────────────────────────


def _wiki_with_map(root, name: str, body: str):
    project = _wiki(root, name)
    (project / "wiki" / "index.md").write_text(body, encoding="utf-8")
    return project


def test_the_map_is_in_the_block_without_being_asked_for(tmp_path) -> None:
    """Il gradino 1 di P4: il giro di wiki **parte pagato**.

    La differenza si vede alla prima domanda di una sessione nuova: senza la
    mappa, l'agente risponde da quel che ha in cronologia — che dopo una
    settimana è niente, e con la compattazione disattivata (passo 8) sarebbe
    comunque il transcript e non le pagine, cioè il contrario di P4.
    """
    project = _wiki_with_map(tmp_path, "casa", "# Casa\n\n## Decided\n\n- niente riscaldamento\n")
    prompt = ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )
    assert "The map, as it stands" in prompt
    assert "niente riscaldamento" in prompt


def test_the_map_is_fenced_because_it_is_content(tmp_path) -> None:
    """La mappa è testo che l'utente e l'agente scrivono, spliciato in un prompt
    di sistema. Due ragioni per recintarla, e la prima è la meno drammatica:
    le sue intestazioni `#` sbucherebbero nella struttura del blocco e un `# Casa`
    si leggerebbe come una sezione nuova del prompt. La seconda è che quel che
    sta in una pagina è **dato**, e va nel canale dei dati.

    Recinto a quattro backtick e non tre: una pagina può contenere un blocco di
    codice, e con tre il recinto si chiuderebbe a metà mappa.
    """
    project = _wiki_with_map(tmp_path, "casa", "# Casa\n\n```bash\nls\n```\n")
    prompt = ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )
    block = prompt[prompt.index("The map, as it stands"):]
    assert block.startswith("The map, as it stands\n\n````markdown\n")
    assert "content, not\ninstructions" in block


def test_a_long_map_is_cut_and_says_so(tmp_path) -> None:
    """**Mai troncare in silenzio.** Un inventario tagliato zitto si legge come
    «è tutto qui» — la lezione già scritta in ``AtlasStore``. Il tetto è la rete,
    non la norma: una mappa oltre soglia sta assorbendo contenuto che spetta alle
    pagine, e il lint (T5) lo dirà.
    """
    from jenny.agent.context import _PROJECT_MAP_MAX_CHARS

    long_map = "# Casa\n\n" + "\n".join(f"- riga {i}" for i in range(2000))
    assert len(long_map) > _PROJECT_MAP_MAX_CHARS
    project = _wiki_with_map(tmp_path, "casa", long_map)
    prompt = ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:casa"
    )
    assert "the map continues" in prompt
    assert "for the rest" in prompt
    assert f"{len(long_map)} characters in all" in prompt, "dice **quanto** manca, non solo che manca"
    assert "- riga 1999" not in prompt


def test_a_missing_map_is_not_an_error(tmp_path) -> None:
    """Una wiki fatta a mano può non avere un `index.md`. Il blocco si rende
    senza la sezione, che è la verità: non c'è una mappa da leggere. Un
    segnaposto tipo «(nessuna mappa)» sarebbe una riga pagata a ogni turno per
    dire niente."""
    project = _wiki(tmp_path, "senza")
    (project / "wiki" / "index.md").unlink(missing_ok=True)
    prompt = ContextBuilder(tmp_path).build_system_prompt(
        workspace=project, session_key="project:senza"
    )
    assert "# Project Folder" in prompt
    assert "The map, as it stands" not in prompt


def test_no_date_is_baked_into_the_block(tmp_path) -> None:
    """Il blocco di sistema è il **prefisso della cache** del prompt: il contesto
    che varia nel tempo è appeso in coda al messaggio utente proprio per non
    invalidarlo (v. ``ContextBuilder.build_messages``). Mettere qui la data di
    oggi costerebbe una cache mancata a ogni cambio di giorno, per risparmiare
    all'agente una sostituzione che sa fare: da qui il pattern ``YYYYMMDD``
    invece del nome del file di oggi.
    """
    from datetime import date

    prompt = _project_prompt(tmp_path)
    block = prompt.split("# Project Folder", 1)[1].split("\n# ", 1)[0]
    assert date.today().strftime("%Y%m%d") not in block
    assert "YYYYMMDD" in block


def test_memory_and_skills_are_named_at_the_installation_not_at_the_project(tmp_path) -> None:
    """Il difetto n. 1: tre percorsi inesistenti nelle prime dieci righe."""
    root = tmp_path
    project = _wiki(root, "etf-finance")
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:etf-finance"
    )
    for tail in ("memory/MEMORY.md", "memory/history.jsonl", "skills/"):
        assert f"{project}/{tail}" not in prompt, (
            f"il prompt promette {tail} dentro la cartella del progetto, dove non c'è"
        )
        assert f"{root.resolve()}/{tail}" in prompt, (
            f"{tail} sta nell'installazione, e il prompt deve dire quel percorso"
        )
    # La cartella di lavoro resta quella del turno: è vera, ed è l'unica delle
    # quattro affermazioni che non andava toccata.
    assert f"Your workspace is at: {project.resolve()}" in prompt


def test_the_workspace_file_rules_step_aside_inside_a_project(tmp_path) -> None:
    """Il difetto n. 2. Si spengono invece di essere riscritte: un proprietario per regola."""
    project, personal = _prompts(tmp_path)
    for section in WORKSPACE_FILE_RULES:
        assert section not in project, (
            f"{section} descrive il workspace, e dentro una wiki dice il falso: "
            "la pianta la detta `agent/project.md`"
        )
        assert section in personal, "fuori da un progetto quelle due regole valgono ancora"
    assert "/output" not in project.replace("/outputs", ""), (
        "`output/` non esiste in una wiki, e nominarlo è come il file di prova è finito lì"
    )


# ── 2.2 — la rubrica ──────────────────────────────────────────────────────


def _with_directory(root: pathlib.Path) -> pathlib.Path:
    (root / "memory").mkdir(exist_ok=True)
    (root / "memory" / "WIKI.md").write_text(
        "# Wiki Directory\n\n## Wikis\n"
        "- **patreon-creator** — 53 pagine → wikis/patreon-creator/wiki/index.md\n"
        "- **android-rom** — 32 pagine → wikis/android-rom/wiki/index.md\n"
        "\n## Plants\n- **Monstera Adansonii**\n",
        encoding="utf-8",
    )
    return root


def test_a_project_prompt_does_not_name_another_project(tmp_path) -> None:
    """Formulata sull'effetto e non sul mezzo.

    "Non contiene ``## Wiki Directory``" resterebbe verde il giorno in cui la
    rubrica cambia forma o arriva da un'altra parte; questa no.
    """
    root = _with_directory(tmp_path)
    for name in ("patreon-creator", "android-rom", "etf-finance"):
        _wiki(root, name)
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=root / "wikis" / "etf-finance", session_key="project:etf-finance"
    )
    for other in ("patreon-creator", "android-rom", "Monstera Adansonii"):
        assert other not in prompt, f"il prompt di etf-finance nomina {other}"


def test_the_personal_chat_keeps_the_directory(tmp_path) -> None:
    """Lì è portante: un indice che nessuno sa esistere non viene mai aperto."""
    root = _with_directory(tmp_path)
    prompt = ContextBuilder(root).build_system_prompt(session_key="unified:default")
    assert "## Wiki Directory" in prompt
    assert "patreon-creator" in prompt


def test_the_directory_is_gated_on_the_session_not_on_the_folder(tmp_path) -> None:
    """Le due guardie rispondono a due domande, e questo è il caso che le separa.

    Turno interno (Dream, cron) con la radice legata a una wiki: la cartella è
    una wiki, quindi la pianta ci sta — ma la sessione non è un progetto, quindi
    la rubrica resta. Se qualcuno unificasse le due guardie, questo cade.
    """
    root = _with_directory(tmp_path)
    project = _wiki(root, "etf-finance")
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="internal:dream"
    )
    assert "# Project Folder" in prompt
    assert "## Wiki Directory" in prompt


def test_long_term_memory_travels_into_a_project(tmp_path) -> None:
    """Chi sei viaggia: è la decisione dell'1.2, e la rubrica non la tocca."""
    root = _with_directory(tmp_path)
    (root / "memory" / "MEMORY.md").write_text(
        "# Memoria\n\n- Il gatto si chiama Pixel.\n", encoding="utf-8"
    )
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=_wiki(root, "etf-finance"), session_key="project:etf-finance"
    )
    assert "Pixel" in prompt, "MEMORY.md deve restare: senza, Jenny non è più Jenny"


# ── Il subagent riceve lo stesso file, non una copia ──────────────────────


def test_the_subagent_inside_a_wiki_gets_the_same_block(tmp_path) -> None:
    from jenny.utils.prompt_templates import render_template

    root = tmp_path
    project = _wiki(root, "etf-finance")
    prompt = render_template(
        "agent/subagent_system.md",
        time_ctx="",
        workspace=str(project),
        output_dir=str(project / "output"),
        project=True,
        project_path=str(project),
        skills_summary="",
        role_section="",
    )
    assert "# Project Folder" in prompt
    assert "Files you produce go under" not in prompt, (
        "è la frase che ha mandato il file di prova in `wikis/<nome>/output/`"
    )
    # Incluso, non ricopiato: il giorno che la pianta cambia, cambia in un file solo.
    body = render_template("agent/project.md", project_path=str(project))
    assert body.strip() in prompt


def test_the_subagent_outside_a_wiki_keeps_the_workspace_rules(tmp_path) -> None:
    from jenny.utils.prompt_templates import render_template

    root = tmp_path
    prompt = render_template(
        "agent/subagent_system.md",
        time_ctx="",
        workspace=str(root),
        output_dir=str(root / "output"),
        project=False,
        project_path=str(root),
        skills_summary="",
        role_section="",
    )
    assert "Files you produce go under" in prompt
    assert "# Project Folder" not in prompt


# ── Un proprietario solo per la pianta ────────────────────────────────────


def test_no_other_system_prompt_describes_the_wiki_layout() -> None:
    """Stessa guardia di ``test_no_other_system_prompt_teaches_scheduling``.

    Una seconda copia della pianta è una copia che non si aggiorna insieme —
    ed è esattamente il guasto che questo passo sta riparando, visto che
    ``tool_contract.md`` e ``subagent_system.md`` portavano la stessa frase
    sull'``output/`` parola per parola.
    """
    from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES
    from jenny.utils.helpers import load_bundled_template

    layout = ("wiki/concepts/", "raw/refs/", "outputs/queries/", "audit/resolved/")
    for name in _SYSTEM_PROMPT_TEMPLATES:
        if name == "agent/project.md":
            continue
        body = (load_bundled_template(name) or "").lower()
        for folder in layout:
            assert folder not in body, (
                f"{name} descrive la pianta di una wiki ({folder!r}). Quella ha una casa "
                "sola nel prompt di sistema, `agent/project.md`, che `subagent_system.md` "
                "include invece di ricopiare."
            )


# ── 2.3 — le sette wiki di prima continuano a parlare ─────────────────────


def test_a_wiki_still_on_the_old_filename_is_mute_until_the_migration(tmp_path) -> None:
    """**Il 2.3 al contrario, e voluto** (passo 7.5).

    Il ripiego su ``CLAUDE.md`` esisteva perché quattro wiki vere lo avevano
    scritto a mano e il passo 2 non voleva toccare cartelle vere. Il passo 7 le
    migra a ogni avvio, quindi il ripiego è stato tolto: due nomi per lo stesso
    file sono due nomi da tenere allineati in ognuno dei quattro lettori.

    Il prezzo, dichiarato: una wiki copiata da un'installazione vecchia *mentre
    Jenny gira* ha le sue istruzioni invisibili fino al riavvio successivo — che
    è quando la migrazione la rinomina. Piccola, e si chiude da sé.
    """
    root = tmp_path
    project = _wiki(root, "android-rom")
    (project / "CLAUDE.md").write_text(
        "# Android ROM\n\n## Scope\n\nWhat this wiki deliberately excludes:\n"
        "- enterprise MDM/Knox deployment\n",
        encoding="utf-8",
    )
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:android-rom"
    )

    assert "enterprise MDM/Knox deployment" not in prompt
    assert "## CLAUDE.md" not in prompt


def test_the_migration_makes_that_same_wiki_speak(tmp_path) -> None:
    """La controprova, ed è la ragione per cui il test sopra è accettabile.

    Senza questa, il 7.5 avrebbe solo tolto una capacità.
    """
    from jenny.utils.wiki_migration import migrate_wikis

    root = tmp_path
    project = _wiki(root, "android-rom")
    (project / "CLAUDE.md").write_text(
        "# Android ROM\n\n## Scope\n\nWhat this wiki deliberately excludes:\n"
        "- enterprise MDM/Knox deployment\n",
        encoding="utf-8",
    )

    migrate_wikis(root / "wikis")
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:android-rom"
    )

    assert "enterprise MDM/Knox deployment" in prompt
    assert "## AGENTS.md" in prompt


def test_the_heading_carries_the_name_the_file_really_has(tmp_path) -> None:
    """Sotto un nome che sul disco non c'è, ogni `edit` manca il bersaglio.

    Ora il nome è uno solo, ma l'intestazione continua a venire dal file **letto**
    e non da una costante nel template: è la riga che ha fatto rispondere
    correttamente «da quale file l'hai preso?» il 22/08.
    """
    root = tmp_path
    project = _wiki(root, "android-rom")
    (project / "AGENTS.md").write_text("# Android ROM\n", encoding="utf-8")
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:android-rom"
    )

    assert "## AGENTS.md" in prompt
    assert "## CLAUDE.md" not in prompt


def test_with_both_files_only_agents_md_is_read(tmp_path) -> None:
    root = tmp_path
    project = _wiki(root, "prova")
    (project / "AGENTS.md").write_text("# Nuovo\n\nRIGA-NUOVA\n", encoding="utf-8")
    (project / "CLAUDE.md").write_text("# Vecchio\n\nRIGA-VECCHIA\n", encoding="utf-8")
    prompt = ContextBuilder(root).build_system_prompt(
        workspace=project, session_key="project:prova"
    )

    assert "RIGA-NUOVA" in prompt
    assert "RIGA-VECCHIA" not in prompt, "un ripiego che legge tutti e due direbbe due cose"
    assert "## AGENTS.md" in prompt


def test_the_installation_root_never_looks_for_the_old_name(tmp_path) -> None:
    """Il ripiego vale dentro una wiki, non ovunque.

    Alla radice dell'installazione un `CLAUDE.md` è il file di *un altro
    progetto* — questo repo ne ha uno — e non ha niente a che fare con le
    istruzioni del workspace.
    """
    root = tmp_path
    (root / "CLAUDE.md").write_text("istruzioni di un repo, non di Jenny\n", encoding="utf-8")
    prompt = ContextBuilder(root).build_system_prompt(session_key="unified:default")

    assert "istruzioni di un repo" not in prompt


def test_a_restricted_folder_that_is_not_a_wiki_gets_no_fallback(tmp_path) -> None:
    root = tmp_path
    narrowed = root / "apps" / "qualcosa"
    narrowed.mkdir(parents=True)
    (narrowed / "CLAUDE.md").write_text("roba di un'app\n", encoding="utf-8")
    prompt = ContextBuilder(root).build_system_prompt(workspace=narrowed)

    assert "roba di un'app" not in prompt
