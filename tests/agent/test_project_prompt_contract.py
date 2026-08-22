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
    ore sono. La profondità — le cinque operazioni, il formato di pagina, lint e
    audit — sta nella skill ``llm-wiki``, che si legge su richiesta.
    """
    from jenny.utils.prompt_templates import render_template

    rendered = render_template("agent/project.md", project_path="/data/workspace/wikis/x")
    assert len(rendered) <= 1500, (
        f"agent/project.md è {len(rendered)} caratteri: sta diventando il manuale della "
        "skill. La pianta sta qui, il come si opera sta in `skills/llm-wiki/SKILL.md`."
    )


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
