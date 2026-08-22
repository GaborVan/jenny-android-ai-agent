"""Creare un progetto dalla WebUI, e vedere i progetti che esistono.

Prima di questo lavoro il chip faceva due cose sbagliate, entrambe silenziose:
elencava `workspace/projects/`, una cartella che non esiste (i progetti **sono**
le wiki, `roadmap/project-sessions.md`), e "Nuovo progetto" chiamava
`/api/workspace/mkdir` — cartella nuda, nessun albero, nessun `AGENTS.md`,
nessuna voce nel registro. Una wiki rotta che sembrava un progetto.

Dal 22/08 (**T1** di `roadmap/taccuino-passi.md`) lo scaffolder e' nel package
(`webui/project_scaffold.py`) e costruisce il **formato nostro**: pagine piatte
sotto `wiki/`, un diario, la mappa. Il fixture monta comunque il checkout della
skill nel workspace, perche' da la' viene ancora `reindex_wikis.py` — il registro
del workspace e' comune ai due formati.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from jenny.webui.commands import (
    MAX_PROJECT_SEED_CHARS,
    CommandContext,
    CommandError,
    dispatch_command,
)
from jenny.webui.workspaces import WebUIWorkspaceController

_REPO = Path(__file__).resolve().parents[2]
_SKILL_SCRIPTS = _REPO / "jenny" / "skills" / "llm-wiki" / "scripts"
_AUTH_SECRET = "test-secret"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace con il checkout della skill, come sul dispositivo."""
    root = tmp_path / "workspace"
    scripts = root / "skills" / "llm-wiki" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(_SKILL_SCRIPTS / "reindex_wikis.py", scripts / "reindex_wikis.py")
    return root


@pytest.fixture
def ctx(workspace: Path) -> CommandContext:
    return CommandContext(get_workspace_root=lambda: workspace)


async def _create(ctx: CommandContext, **params) -> dict:
    return await dispatch_command(ctx, "project.create", params)


# ── quel che il pulsante deve produrre ───────────────────────────────────────


def _frontmatter(text: str) -> dict:
    """La frontmatter di *text*, parsata. Solleva se il blocco non è YAML valido —
    che è il punto: una riga di scope scritta dall'utente non deve poterlo rompere."""
    import yaml

    block = text.split("---", 2)[1]
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict), f"frontmatter non parsabile: {block!r}"
    return parsed


class TestUnProgettoNasceCompleto:
    async def test_crea_lalbero_il_registro_e_la_riga_di_scope(self, ctx, workspace):
        result = await _create(ctx, name="patreon-creator", seed="Come si cresce su Patreon.")

        root = workspace / "wikis" / "patreon-creator"
        for rel in ("AGENTS.md", "wiki/index.md", "audit/.gitkeep"):
            assert (root / rel).is_file(), rel
        for rel in ("wiki", "raw/journal", "raw/research", "log", "audit/resolved"):
            assert (root / rel).is_dir(), rel
        assert result["name"] == "patreon-creator"
        assert result["seeded"] is True

        # La riga dell'utente sta dove il registro la va a prendere...
        schema = (root / "AGENTS.md").read_text(encoding="utf-8")
        # Quotata, e provata **parsando** invece di confrontando una stringa: la
        # vecchia asserzione fissava la forma non quotata, cioè esattamente il
        # difetto che il 22/08 ha fatto perdere tutta la frontmatter a una wiki
        # la cui riga di scope conteneva un due punti.
        assert _frontmatter(schema)["summary"] == "Come si cresce su Patreon."
        # ...e nella mappa, che è quel che l'agente legge per primo (T3).
        assert "Come si cresce su Patreon." in (root / "wiki" / "index.md").read_text("utf-8")
        # Nessun segnaposto: il seme entra alla nascita, non per sostituzione.
        assert "<one-line scope" not in schema
        # ...e infatti nel registro c'e'.
        registry = (workspace / "wikis" / "_index.md").read_text(encoding="utf-8")
        assert "Come si cresce su Patreon." in registry
        assert "[[patreon-creator/wiki/index|patreon-creator]]" in registry

    async def test_non_crea_la_tassonomia_del_pattern_di_ricerca(self, ctx, workspace):
        """T1: le cartelle che obbligavano a scegliere «concept o entity?» **mentre**
        si prende un appunto non esistono più. Il pattern document-first resta, ma
        vive nella skill e nelle sette wiki che ce l'hanno già."""
        await _create(ctx, name="nuovo", seed="x")

        root = workspace / "wikis" / "nuovo"
        for rel in ("wiki/concepts", "wiki/entities", "wiki/summaries",
                    "outputs/queries", "raw/papers", "raw/articles", "raw/refs"):
            assert not (root / rel).exists(), rel

    async def test_il_titolo_viene_dal_nome_della_cartella(self, ctx, workspace):
        await _create(ctx, name="patreon-creator", seed="x")

        schema = (workspace / "wikis" / "patreon-creator" / "AGENTS.md").read_text("utf-8")
        assert "# Patreon Creator" in schema

    async def test_e_vuoto_di_contenuto(self, ctx, workspace):
        """"Nuovo" costruisce lo scaffolding, non un primo articolo."""
        await _create(ctx, name="nuovo", seed="x")

        root = workspace / "wikis" / "nuovo"
        # Sotto `wiki/` c'è la mappa e nient'altro: le pagine le scrive il lavoro.
        assert [p.name for p in (root / "wiki").iterdir()] == ["index.md"]
        # Il diario nasce vuoto: la prima pagina la scrive la prima cattura, e un
        # file creato qui sarebbe il diario di un giorno in cui non si è detto niente.
        assert list((root / "raw" / "journal").iterdir()) == []
        assert list((root / "raw" / "research").iterdir()) == []

    async def test_la_mappa_nasce_con_le_sue_sezioni(self, ctx, workspace):
        """Le sezioni nascono vuote ma nascono: il giardiniere (T4) aggiorna
        sezioni che esistono, invece di inventarsi una struttura ogni volta — che è
        il modo in cui due sessioni diverse producono due mappe diverse."""
        await _create(ctx, name="nuovo", seed="di cosa si tratta")

        mappa = (workspace / "wikis" / "nuovo" / "wiki" / "index.md").read_text("utf-8")
        for section in ("## Decided", "## Open", "## Pages"):
            assert section in mappa, section
        # Il diario è citato come percorso, **non** come `[[link]]`: sta fuori da
        # `wiki/`, e un wikilink fuori dalle pagine è morto per `resolve_wikilink`.
        assert "raw/journal" in mappa
        assert "[[raw/journal" not in mappa

    async def test_rilanciarlo_non_riscrive_niente(self, ctx, workspace):
        """Lo scaffolder scrive solo quel che manca: è la regola che rende sicuro
        ripassare su una cartella rimasta a metà."""
        from jenny.webui.project_scaffold import scaffold_project

        await _create(ctx, name="nuovo", seed="x")
        root = workspace / "wikis" / "nuovo"
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

        assert scaffold_project(root, "Nuovo", "x", '"x"') == []
        assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before

    async def test_una_wiki_esistente_non_viene_toccata(self, ctx, workspace):
        """Il secondo progetto non deve poter riscrivere il primo."""
        await _create(ctx, name="primo", seed="il primo")
        before = (workspace / "wikis" / "primo" / "wiki" / "index.md").read_bytes()

        await _create(ctx, name="secondo", seed="il secondo")

        assert (workspace / "wikis" / "primo" / "wiki" / "index.md").read_bytes() == before


# ── quel che il comando deve rifiutare ───────────────────────────────────────


class TestIlGateStaSulServer:
    async def test_rifiuta_un_progetto_che_esiste(self, ctx):
        await _create(ctx, name="patreon", seed="uno")

        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="patreon", seed="due")
        assert exc.value.code == "bad_request"

    @pytest.mark.parametrize(
        "name",
        [
            "../fuori",           # traversal
            "sotto/cartella",     # separatore
            ".nascosto",          # cartella nascosta dentro wikis/
            "..",
            "",
            "   ",
            "a" * 65,             # oltre il tetto
        ],
    )
    async def test_rifiuta_un_nome_non_valido(self, ctx, workspace, name):
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name=name, seed="x")
        assert exc.value.code == "bad_request"
        # E non lascia niente per terra: il gate scatta prima del filesystem.
        assert not (workspace / "wikis").exists()

    @pytest.mark.parametrize("seed", ["", "   ", "\n\n"])
    async def test_rifiuta_un_progetto_senza_riga_di_scope(self, ctx, workspace, seed):
        """*"Devi scrivere tu qualcosa, sennò la chat è ferma"* — e' una regola,
        non un suggerimento del dialogo: vale anche per un client che non chiede."""
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="muto", seed=seed)
        assert exc.value.code == "bad_request"
        assert not (workspace / "wikis").exists()

    async def test_rifiuta_una_riga_troppo_lunga(self, ctx):
        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="lungo", seed="x" * (MAX_PROJECT_SEED_CHARS + 1))
        assert exc.value.code == "too_large"

    async def test_richiude_gli_a_capo_invece_di_fallire(self, ctx, workspace):
        """Il frontmatter e' YAML: una riga sola. Su una tastiera mobile un
        a-capo di troppo non deve costare un errore."""
        await _create(ctx, name="multi", seed="prima riga\nseconda   riga\n")

        schema = (workspace / "wikis" / "multi" / "AGENTS.md").read_text("utf-8")
        assert _frontmatter(schema)["summary"] == "prima riga seconda riga"

    async def test_un_workspace_senza_la_skill_crea_comunque_il_progetto(self, tmp_path: Path):
        """Fino al 22/08 questo era un errore: lo scaffolder stava nel checkout
        della skill, quindi un workspace senza `skills/` non poteva creare niente.
        Ora lo scaffolder e' nel package e dalla skill viene solo `reindex_wikis`,
        che aggiorna il **registro**: se manca, il progetto nasce completo e il
        registro resta indietro — un inconveniente che `lint --workspace` ripara,
        non un fallimento della creazione."""
        empty = tmp_path / "vuoto"
        empty.mkdir()
        ctx = CommandContext(get_workspace_root=lambda: empty)

        result = await _create(ctx, name="x", seed="y")

        assert result["registry"] is None
        assert (empty / "wikis" / "x" / "wiki" / "index.md").is_file()
        assert (empty / "wikis" / "x" / "raw" / "journal").is_dir()


# ── l'elenco che il chip legge ───────────────────────────────────────────────


@pytest.fixture
def handler(workspace: Path, monkeypatch):
    from jenny.config import paths as paths_mod
    from jenny.webui.ws_http import GatewayHTTPHandler

    monkeypatch.setattr(paths_mod, "get_workspace_path", lambda: workspace)
    # Controller vero e non un mock: la route ci legge lo scope da mettere nel
    # payload, ed e' proprio quello che questi test verificano.
    workspaces = WebUIWorkspaceController(
        session_manager=None,
        default_workspace=workspace,
        default_restrict_to_workspace=True,
    )
    return GatewayHTTPHandler(
        config=SimpleNamespace(
            workspace=SimpleNamespace(enabled=True),
            wiki=SimpleNamespace(enabled=True, wikis_dir="wikis"),
            token_issue_secret=_AUTH_SECRET,
            verbose=False,
        ),
        session_manager=None,
        runtime_model_name=lambda: "test-model",
        bus=MagicMock(),
        media=MagicMock(),
        workspaces=workspaces,
        skills_workspace_path=workspace,
    )


async def _get_projects(handler) -> dict:
    request = WsRequest(path=f"/api/projects?token={_AUTH_SECRET}", headers=Headers())
    response = await handler.wiki_routes.dispatch(request, "/api/projects")
    assert response is not None, "route not registered"
    assert response.status_code == 200, response.body
    return json.loads(response.body.decode("utf-8"))


class TestElencoProgetti:
    async def test_elenca_le_wiki_e_non_una_cartella_projects(self, handler, ctx, workspace):
        await _create(ctx, name="alpha", seed="a")
        await _create(ctx, name="beta", seed="b")
        # Una cartella `projects/` accanto: non deve entrare nell'elenco.
        (workspace / "projects" / "fantasma").mkdir(parents=True)

        payload = await _get_projects(handler)

        assert payload["dir"] == "wikis"
        assert [p["name"] for p in payload["projects"]] == ["alpha", "beta"]
        assert all(isinstance(p["modified"], int) for p in payload["projects"])

    async def test_una_cartella_senza_wiki_non_e_un_progetto(self, handler, ctx, workspace):
        await _create(ctx, name="vera", seed="v")
        (workspace / "wikis" / "solo-una-cartella").mkdir()

        payload = await _get_projects(handler)

        assert [p["name"] for p in payload["projects"]] == ["vera"]

    async def test_nessun_progetto_non_e_un_errore(self, handler):
        payload = await _get_projects(handler)
        assert payload["projects"] == []

    async def test_serve_il_token(self, handler):
        request = WsRequest(path="/api/projects", headers=Headers())
        response = await handler.wiki_routes.dispatch(request, "/api/projects")
        assert response is not None and response.status_code == 401


# ── il chip non deve tornare a leggere la cartella sbagliata ─────────────────


def test_il_chip_non_legge_piu_una_cartella_projects():
    """Guardia contro il ritorno del difetto: il chip elencava
    `workspace/projects/` con `listWorkspace`, e quella cartella non esiste."""
    source = (
        _REPO / "jenny" / "templates" / "ui" / "assets" / "shared" / "scope-chip.js"
    ).read_text(encoding="utf-8")

    assert "listWorkspace" not in source
    assert "createWorkspaceFolder" not in source
    assert "api.listProjects()" in source
    assert "rpc.createProject(" in source


# ── aprire la chat di un progetto ────────────────────────────────────────────


async def _get_thread(handler, key: str):
    quoted = urllib.parse.quote(key, safe="")
    request = WsRequest(
        path=f"/api/sessions/{quoted}/webui-thread?token={_AUTH_SECRET}", headers=Headers()
    )
    return handler._handle_webui_thread_get(request, quoted)


class TestIlThreadDiUnProgetto:
    """La route che serve la conversazione disegnata.

    La sua guardia accettava solo chiavi `websocket:*`, quindi avrebbe risposto
    **404 a ogni progetto**: la chat di un progetto non si sarebbe potuta aprire,
    e il sintomo sarebbe stato una schermata vuota senza errori nel client.
    """

    async def test_una_chiave_di_progetto_non_e_piu_404(self, handler, ctx, workspace):
        await _create(ctx, name="patreon", seed="di cosa si occupa")

        response = await _get_thread(handler, "project:patreon")

        assert response.status_code != 404, response.body

    async def test_il_payload_porta_la_cartella_del_progetto(self, handler, ctx, workspace):
        """E la porta **prima del primo messaggio**.

        Il chip legge lo scope da qui: leggendolo dai metadati della sessione —
        che non esistono finché non si scrive — un progetto appena aperto avrebbe
        mostrato "sessione personale" sopra il composer. Cioè esattamente la cosa
        che il chip esiste per non fare.
        """
        await _create(ctx, name="patreon", seed="di cosa si occupa")

        response = await _get_thread(handler, "project:patreon")
        payload = json.loads(response.body.decode("utf-8"))

        scope = payload["workspace_scope"]
        assert scope["project_name"] == "patreon"
        assert scope["project_path"].endswith("wikis/patreon")
        assert scope["access_mode"] == "restricted"

    async def test_una_sessione_interna_resta_illeggibile(self, handler):
        """Il lato del confine che non doveva allargarsi."""
        for key in ("cron:job-1", "subagent:L1", "heartbeat", "dream:20260821"):
            response = await _get_thread(handler, key)
            assert response.status_code == 404, key
