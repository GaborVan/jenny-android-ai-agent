"""Creare un progetto dalla WebUI, e vedere i progetti che esistono.

Prima di questo lavoro il chip faceva due cose sbagliate, entrambe silenziose:
elencava `workspace/projects/`, una cartella che non esiste (i progetti **sono**
le wiki, `roadmap/project-sessions.md`), e "Nuovo progetto" chiamava
`/api/workspace/mkdir` — cartella nuda, nessun albero, nessun `AGENTS.md`,
nessuna voce nel registro. Una wiki rotta che sembrava un progetto.

Il fixture monta lo scaffolder **vero** della skill nel workspace, perche' e'
quello che gira in produzione: il comando lo carica da `workspace/skills/`, non
lo reimplementa.
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
    for name in ("scaffold.py", "reindex_wikis.py"):
        shutil.copy(_SKILL_SCRIPTS / name, scripts / name)
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
        for rel in ("raw/articles", "raw/papers", "raw/notes", "raw/refs", "outputs/queries"):
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
        assert "- Come si cresce su Patreon." in schema
        assert "<one-line scope" not in schema
        # ...e infatti nel registro c'e'.
        registry = (workspace / "wikis" / "_index.md").read_text(encoding="utf-8")
        assert "Come si cresce su Patreon." in registry
        assert "[[patreon-creator/wiki/index|patreon-creator]]" in registry

    async def test_il_titolo_viene_dal_nome_della_cartella(self, ctx, workspace):
        await _create(ctx, name="patreon-creator", seed="x")

        schema = (workspace / "wikis" / "patreon-creator" / "AGENTS.md").read_text("utf-8")
        assert "# Patreon Creator" in schema

    async def test_e_vuoto_di_contenuto(self, ctx, workspace):
        """"Nuovo" costruisce lo scaffolding, non un primo articolo."""
        await _create(ctx, name="nuovo", seed="x")

        root = workspace / "wikis" / "nuovo"
        assert list((root / "wiki" / "concepts").iterdir()) == []
        assert list((root / "raw" / "notes").iterdir()) == []

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

    async def test_dice_chiaro_quando_manca_lo_scaffolder(self, tmp_path: Path):
        """Lo script sta nel workspace ed e' modificabile dall'utente: se non
        c'e', l'errore deve nominarlo, non arrivare come "internal"."""
        empty = tmp_path / "vuoto"
        empty.mkdir()
        ctx = CommandContext(get_workspace_root=lambda: empty)

        with pytest.raises(CommandError) as exc:
            await _create(ctx, name="x", seed="y")
        assert exc.value.code == "bad_request"
        assert "scaffold" in str(exc.value).lower()


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
