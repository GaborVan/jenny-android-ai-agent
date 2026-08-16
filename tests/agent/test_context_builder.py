"""Tests for ContextBuilder — system prompt and message assembly."""

import hashlib
from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder
from jenny.session.goal_state import GOAL_STATE_KEY
from jenny.utils.android_assets import _RETIRED_TEMPLATE_DIGESTS, _USER_OWNED_TEMPLATES
from jenny.utils.helpers import merge_message_content

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _builder(tmp_path: Path, **kw) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


# ---------------------------------------------------------------------------
# _build_runtime_context (static)
# ---------------------------------------------------------------------------


class TestBuildRuntimeContext:
    def test_time_only(self):
        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "[Runtime Context" in ctx
        assert "[/Runtime Context]" in ctx
        assert "Current Time:" in ctx
        assert "Channel:" not in ctx

    def test_with_channel_and_chat_id(self):
        ctx = ContextBuilder._build_runtime_context("websocket", "chat123")
        assert "Channel: websocket" in ctx
        assert "Chat ID: chat123" in ctx

    def test_with_sender_id(self):
        ctx = ContextBuilder._build_runtime_context("internal", "direct", sender_id="user1")
        assert "Sender ID: user1" in ctx

    def test_with_timezone(self):
        ctx = ContextBuilder._build_runtime_context(None, None, timezone="Asia/Shanghai")
        assert "Current Time:" in ctx

    def test_no_channel_no_chat_id_omits_both(self):
        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "Channel:" not in ctx
        assert "Chat ID:" not in ctx

    def test_no_sender_id_omits(self):
        ctx = ContextBuilder._build_runtime_context("internal", "direct")
        assert "Sender ID:" not in ctx


# ---------------------------------------------------------------------------
# merge_message_content (helper condiviso in jenny.utils.helpers)
# ---------------------------------------------------------------------------


class TestMergeMessageContent:
    def test_str_plus_str(self):
        result = merge_message_content("hello", "world")
        assert result == "hello\n\nworld"

    def test_empty_left_plus_str(self):
        result = merge_message_content("", "world")
        assert result == "world"

    def test_list_plus_list(self):
        left = [{"type": "text", "text": "a"}]
        right = [{"type": "text", "text": "b"}]
        result = merge_message_content(left, right)
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "b"

    def test_str_plus_list(self):
        right = [{"type": "text", "text": "b"}]
        result = merge_message_content("hello", right)
        assert len(result) == 2
        assert result[0]["text"] == "hello"
        assert result[1]["text"] == "b"

    def test_list_plus_str(self):
        left = [{"type": "text", "text": "a"}]
        result = merge_message_content(left, "world")
        assert len(result) == 2
        assert result[0]["text"] == "a"
        assert result[1]["text"] == "world"

    def test_none_plus_str(self):
        result = merge_message_content(None, "hello")
        assert result == [{"type": "text", "text": "hello"}]

    def test_str_plus_none(self):
        result = merge_message_content("hello", None)
        assert result == [{"type": "text", "text": "hello"}]

    def test_none_plus_none(self):
        result = merge_message_content(None, None)
        assert result == []

    def test_list_items_not_dicts_wrapped(self):
        result = merge_message_content(["raw_item"], None)
        assert result == [{"type": "text", "text": "raw_item"}]


# ---------------------------------------------------------------------------
# _load_bootstrap_files
# ---------------------------------------------------------------------------


class TestLoadBootstrapFiles:
    def test_no_bootstrap_files(self, tmp_path):
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "Be helpful." in result

    def test_multiple_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        (tmp_path / "SOUL.md").write_text("Soul.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## AGENTS.md" in result
        assert "## SOUL.md" in result
        assert "Rules." in result
        assert "Soul." in result

    def test_all_bootstrap_files(self, tmp_path):
        for name in ContextBuilder.BOOTSTRAP_FILES:
            (tmp_path / name).write_text(f"Content of {name}", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        for name in ContextBuilder.BOOTSTRAP_FILES:
            assert f"## {name}" in result

    def test_legacy_tools_md_is_not_bootstrapped(self, tmp_path):
        (tmp_path / "TOOLS.md").write_text("workspace tool notes", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "TOOLS.md" not in result
        assert "workspace tool notes" not in result

    def test_utf8_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("用中文回复", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "用中文回复" in result


# ---------------------------------------------------------------------------
# _load_bootstrap_files — guardia sul template intatto
# ---------------------------------------------------------------------------


def _bundled(name: str) -> str:
    from importlib.resources import files as pkg_files

    tpl = pkg_files("jenny") / "templates" / name
    if not tpl.is_file():
        pytest.skip(f"{name} template not bundled")
    return tpl.read_text(encoding="utf-8")


def _fixture(name: str) -> str:
    """Un template ritirato, letto da ``fixtures/`` e non da una stringa qui dentro.

    È la stessa ragione registrata da ``97d7b38``: alcune di quelle righe
    finiscono con uno spazio, trascritte in un sorgente Python le toglierebbe
    ``ruff`` (W291), il digest non combacerebbe più e il test proverebbe
    qualcosa di diverso da quello che c'è sui telefoni. I file sono estratti con
    ``git show <sha>:jenny/templates/<nome>``, non ricopiati a mano.
    """
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def _retired_user_template() -> str:
    """``jenny/templates/USER.md`` come spediva da 0.3.0 (8833b94) a 0.7.1."""
    return _fixture("user_md_retired_0.3.0.md")


# Le tre versioni ritirate di ``AGENTS.md``. Tutte e tre sono candidate vive: un
# telefono porta quella che era bundled al *suo* primo avvio, per sempre.
_RETIRED_AGENTS_FIXTURES = [
    "agents_md_retired_v0.3.0.md",
    "agents_md_retired_6c5dba8_unreleased.md",
    "agents_md_retired_v0.6.6.md",
]


class TestBootstrapTemplateGuard:
    """Un file di bootstrap mai toccato non è contenuto scritto dall'utente.

    ``USER.md`` e ``AGENTS.md`` intatti non dicono niente né sull'utente né sul
    workspace e vengono omessi (stessa risposta che ``MEMORY.md`` riceve in
    ``build_system_prompt``); ``SOUL.md`` intatto è l'identità di serie e resta,
    ma etichettato.
    """

    def test_untouched_user_md_is_skipped(self, tmp_path):
        (tmp_path / "USER.md").write_text(_bundled("USER.md"), encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert result == ""

    def test_edited_user_md_is_injected_without_notice(self, tmp_path):
        (tmp_path / "USER.md").write_text("- **Name**: Luca", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## USER.md" in result
        assert "Luca" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_untouched_agents_md_is_skipped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(_bundled("AGENTS.md"), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    @pytest.mark.parametrize("filename", ["SOUL.md"])
    def test_untouched_default_is_kept_but_labelled(self, tmp_path, filename):
        content = _bundled(filename)
        (tmp_path / filename).write_text(content, encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert f"## {filename}" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE in result
        # L'identità di serie non deve sparire: niente regressione di
        # personalità su un'installazione nuova.
        assert content.strip() in result

    @pytest.mark.parametrize("filename", ["AGENTS.md", "SOUL.md"])
    def test_edited_file_carries_no_notice(self, tmp_path, filename):
        # Un file *modificato* entra nel prompt in entrambi i casi: quello che
        # cambia con lo skip è solo il ramo del template intatto.
        (tmp_path / filename).write_text(_bundled(filename) + "\n\nExtra rule.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "Extra rule." in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_pristine_workspace_omits_both_placeholders(self, tmp_path):
        for name in ContextBuilder.BOOTSTRAP_FILES:
            (tmp_path / name).write_text(_bundled(name), encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "## SOUL.md" in result
        assert "## AGENTS.md" not in result
        assert "## USER.md" not in result


class TestRetiredTemplates:
    """Riscrivere un template non deve promuovere i vecchi a scrittura dell'utente.

    Il riconoscimento confronta con la copia bundled *corrente*, quindi ogni
    riscrittura scollegherebbe le installazioni seedate con la precedente e mai
    toccate da Dream: il modulo a caselle tornerebbe nel prompt, per giunta senza
    etichetta perché prenderebbe il ramo "modificato dall'utente".
    """

    def test_retired_user_template_is_still_recognised(self, tmp_path):
        (tmp_path / "USER.md").write_text(_retired_user_template(), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_retired_digest_does_not_swallow_real_user_content(self, tmp_path):
        (tmp_path / "USER.md").write_text(
            _retired_user_template() + "\n- **Name**: Luca\n", encoding="utf-8"
        )
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "Luca" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_current_user_template_digest_is_pinned(self):
        """Chi riscrive ``USER.md`` deve ritirare esplicitamente la versione uscente.

        Senza questo, il prossimo rewrite reintroduce il difetto in silenzio: si
        accorgerebbe solo un'installazione vergine aggiornata mesi dopo. Se questo
        test fallisce, aggiungi il digest qui atteso a
        ``_RETIRED_TEMPLATE_DIGESTS["USER.md"]`` (in
        ``jenny/utils/android_assets.py``) con l'etichetta della sua finestra di
        release, e sostituiscilo con quello nuovo.
        """
        current = hashlib.sha256(
            _bundled("USER.md").strip().encode("utf-8")
        ).hexdigest()
        assert current == "e23a60be0336c5220d3d0dbd256907f66b590156459422a244dcd24685eb49b7"
        assert current not in _RETIRED_TEMPLATE_DIGESTS["USER.md"]

    @pytest.mark.parametrize("fixture", _RETIRED_AGENTS_FIXTURES)
    def test_every_retired_agents_digest_is_recognised(self, tmp_path, fixture):
        """Nessuna delle tre versioni di "# Agent Instructions" torna nel prompt.

        Il manuale di cron che spediva dentro ``AGENTS.md`` ora vive in
        ``agent/scheduling.md``; la copia rimasta sul disco di un telefono è
        testo nostro, ritirato e in due casi su tre pure contraddetto.
        """
        (tmp_path / "AGENTS.md").write_text(_fixture(fixture), encoding="utf-8")
        builder = _builder(tmp_path)
        assert builder._load_bootstrap_files() == ""

    def test_retired_agents_digest_does_not_swallow_real_user_content(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text(
            _fixture("agents_md_retired_v0.3.0.md") + "\n- Deploy con `./gradlew`.\n",
            encoding="utf-8",
        )
        builder = _builder(tmp_path)
        result = builder._load_bootstrap_files()
        assert "./gradlew" in result
        assert ContextBuilder._BOOTSTRAP_TEMPLATE_NOTICE not in result

    def test_current_agents_template_digest_is_pinned(self):
        """Chi riscrive ``AGENTS.md`` deve ritirare esplicitamente la versione uscente.

        Gemello di ``test_current_user_template_digest_is_pinned``: senza,
        il prossimo rewrite rimette il testo uscente nel prompt di ogni
        installazione vergine, per giunta senza etichetta. Se questo test
        fallisce, sposta il digest qui atteso dentro
        ``_RETIRED_TEMPLATE_DIGESTS["AGENTS.md"]`` (in
        ``jenny/utils/android_assets.py``) e mettine qui quello nuovo.
        """
        current = hashlib.sha256(
            _bundled("AGENTS.md").strip().encode("utf-8")
        ).hexdigest()
        assert current == "f7168ac0aacf6424203c6173e46ed333981f57c3d491f055fe1358c9b9614569"
        assert current not in _RETIRED_TEMPLATE_DIGESTS["AGENTS.md"]

    def test_a_template_with_retired_versions_is_never_labelled(self):
        """L'etichetta dice "questo file è ancora il template spedito con l'app",
        e su una versione ritirata sarebbe una bugia: spedita lo era, ma un'altra
        volta e da un'altra versione.

        Che oggi non possa succedere è vero e sta scritto in un commento in
        ``context.py``: chi ha digest ritirati sta in ``_BOOTSTRAP_SKIP_IF_TEMPLATE``,
        quindi non arriva mai al ramo che etichetta. Un commento però non è una
        guardia — basta ritirare una versione di ``SOUL.md`` (l'unico file che
        l'etichetta la riceve davvero) perché la frase inizi a mentire, in
        silenzio e su ogni installazione seedata in quella finestra. Vale qui la
        stessa regola dei digest un file più in là: non è un promemoria, il test
        fallisce finché non lo si fa.

        Le due uscite sono entrambe buone: aggiungere il nome a
        ``_BOOTSTRAP_SKIP_IF_TEMPLATE``, oppure dare a quel ramo una seconda
        etichetta che dica la verità su una versione ritirata.
        """
        for name in _RETIRED_TEMPLATE_DIGESTS:
            if name not in ContextBuilder.BOOTSTRAP_FILES:
                # Non è un file di bootstrap: quel ramo non lo vede proprio.
                # ``memory/MEMORY.md`` ha una guardia sua (``_is_template_content``
                # in ``build_system_prompt``), che omette e non etichetta.
                continue
            assert name in ContextBuilder._BOOTSTRAP_SKIP_IF_TEMPLATE, (
                f"{name} ha versioni ritirate e può ricevere "
                "_BOOTSTRAP_TEMPLATE_NOTICE, che su una versione ritirata è falsa: "
                "aggiungilo a _BOOTSTRAP_SKIP_IF_TEMPLATE o dai a quel ramo "
                "un'etichetta che dica la verità"
            )


# ---------------------------------------------------------------------------
# agent/scheduling.md — dove va un lavoro ricorrente
# ---------------------------------------------------------------------------

# Il titolo del template. Cercarlo nel prompt è come si distingue "il blocco c'è"
# da "il prompt nomina cron da qualche altra parte".
_SCHEDULING_HEADING = "# Recurring Work"

# Il registry che ``build_dream_tools`` costruisce davvero (``memory.py``):
# lettura e scrittura, nient'altro. Nessun ``cron``.
_DREAM_TOOLS = ["read_file", "write_file", "edit_file", "apply_patch"]


class TestSchedulingBlock:
    """La guida su cron/heartbeat va solo a chi il tool ce l'ha.

    Stava nel template di ``AGENTS.md``, cioè in un file che si crea al primo
    avvio e non si aggiorna mai più, e ci andava *sempre*: anche a Dream e ad
    Atlas, che ``cron`` non l'hanno mai avuto.
    """

    def test_scheduling_block_is_rendered_when_cron_is_available(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=["cron", "read_file"])
        assert _SCHEDULING_HEADING in prompt
        assert "mode='monitor'" in prompt

    def test_scheduling_block_is_absent_without_the_cron_tool(self, tmp_path):
        """Il difetto che la guardia chiude: Dream riceveva l'istruzione di
        schedulare con un tool che il suo registry non contiene."""
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=_DREAM_TOOLS)
        assert _SCHEDULING_HEADING not in prompt

    def test_scheduling_block_renders_when_tools_are_unknown(self, tmp_path):
        """``None`` vuol dire "non lo so", non "il tool non c'è".

        Nessun registry per-turno e nessuna callable dal costruttore: si rende,
        come faceva ``AGENTS.md``. Senza questo, un refactor può trasformare la
        guardia in un silenzio senza che niente se ne accorga.
        """
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt(available_tools=None)
        assert _SCHEDULING_HEADING in prompt

    def test_cron_tool_name_constant_matches(self):
        """``_CRON_TOOL_NAME`` non importa ``CronTool``; questo test sì.

        L'import sta qui e non in ``context.py`` perché lì tirerebbe dentro
        tutto il package cron in un modulo che importa mezzo repo.
        """
        from jenny.agent.context import _CRON_TOOL_NAME
        from jenny.agent.tools.cron import CronTool

        # ``name`` è una property e non serve un servizio cron per leggerla:
        # ``None`` basta, il tool non viene usato.
        assert _CRON_TOOL_NAME == CronTool(cron_service=None).name  # type: ignore[arg-type]

    def test_scheduling_block_stays_small(self):
        """Un tetto, non un'abitudine.

        Questo blocco si paga a ogni turno in cui il tool esiste, compresi
        quelli in cui l'utente chiede che ore sono. È l'unico meccanismo che
        tiene fermo il confine fra i quattro posti in cui questa regola vive.
        """
        from jenny.utils.prompt_templates import render_template

        # 1300 e non 1600: il testo reso ne occupa 1134, e un tetto che lascia il
        # 40% di margine non dice mai di no — cioè non fa il suo mestiere. Chi ha
        # bisogno di sforarlo ha quasi sempre bisogno della skill.
        rendered = render_template("agent/scheduling.md")
        assert len(rendered) <= 1300, (
            f"agent/scheduling.md è {len(rendered)} caratteri: sta tornando un manuale. "
            "La profondità — sintassi, fusi orari, esempi, semantica di `list` — va in "
            "`skills/cron/SKILL.md`, che si legge su richiesta invece che a ogni turno."
        )

    def test_no_other_system_prompt_teaches_scheduling(self):
        """Un solo posto nel prompt di sistema, altrimenti la guardia non serve.

        Trovato da una rilettura: ``agent/tool_contract.md`` portava una sezione
        "Scheduling and Background Work" che diceva le stesse tre cose — usa
        ``cron``, aggiorna ``HEARTBEAT.md``, non scrivere il promemoria in
        memoria — e la portava **sempre**, perché quel template non è gated.
        Quindi Dream continuava a ricevere l'istruzione di schedulare con un
        tool che il suo registry non contiene: la guardia di ``scheduling.md``
        copriva una copia su due, che è come non averla.

        La regola sta in ``agent/scheduling.md`` (dietro il tool) e in
        ``skills/cron/SKILL.md`` (su richiesta). Nessun altro template di
        sistema la ripete.
        """
        from jenny.utils.android_assets import _SYSTEM_PROMPT_TEMPLATES
        from jenny.utils.helpers import load_bundled_template

        # Frasi che *insegnano a schedulare*. Nominare `HEARTBEAT.md` come file
        # del workspace resta legittimo (lo fa "Where Produced Files Go"), quindi
        # si cercano le istruzioni, non il nome.
        teaching = ("use the cron tool", "mode='reminder'", "mode='monitor'",
                    "for heartbeat tasks", "recurring jobs")
        for name in _SYSTEM_PROMPT_TEMPLATES:
            if name == "agent/scheduling.md":
                continue
            body = (load_bundled_template(name) or "").lower()
            for phrase in teaching:
                assert phrase not in body, (
                    f"{name} insegna a schedulare ({phrase!r}). Quella regola ha una casa "
                    "sola nel prompt di sistema, `agent/scheduling.md`, perché è l'unica "
                    "resa solo quando il tool `cron` esiste davvero nel turno."
                )


# ---------------------------------------------------------------------------
# I template dell'utente non contengono guida di sistema
# ---------------------------------------------------------------------------


class TestUserOwnedTemplatesCarryNoSystemGuidance:
    """Un file che si crea una volta sola non può contenere una regola che cambia.

    È il difetto di ``AGENTS.md`` reso controllabile: ``_USER_OWNED_TEMPLATES``
    si estrae con ``skip_existing=True``, quindi qualunque cosa ci si scriva
    dentro non raggiunge mai un'installazione esistente.
    """

    def test_the_stale_cron_parameters_are_gone(self):
        """``USER_ID``/``CHANNEL`` e ``web:default`` non esistono.

        Il tool ``cron`` non ha né ``user_id`` né ``channel``
        (``_CRON_PARAMETERS``) e ``jenny/session/keys.py`` conia
        ``unified:default``/``websocket:default``: quella riga diceva al modello
        di procurarsi valori per parametri che non ci sono.
        """
        text = _bundled("AGENTS.md")
        assert "USER_ID" not in text
        assert "web:default" not in text

    @pytest.mark.parametrize("name", _USER_OWNED_TEMPLATES)
    def test_no_user_owned_template_documents_the_tools(self, name):
        text = _bundled(name).lower()
        for token in ("cron", "mode=", "every_seconds", "apply_patch"):
            assert token not in text, (
                f"{name} documenta `{token}`: è guida di sistema in un file che si "
                "crea al primo avvio e non si aggiorna mai più. Va sotto "
                "`jenny/templates/agent/` o in una skill."
            )


# ---------------------------------------------------------------------------
# _is_template_content (static)
# ---------------------------------------------------------------------------


class TestIsTemplateContent:
    def test_nonexistent_template_returns_false(self):
        assert ContextBuilder._is_template_content("anything", "nonexistent/path.md") is False

    def test_content_matching_template(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("jenny") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        original = tpl.read_text(encoding="utf-8")
        assert ContextBuilder._is_template_content(original, "memory/MEMORY.md") is True

    def test_modified_content_returns_false(self):
        from importlib.resources import files as pkg_files
        tpl = pkg_files("jenny") / "templates" / "memory" / "MEMORY.md"
        if not tpl.is_file():
            pytest.skip("MEMORY.md template not bundled")
        assert ContextBuilder._is_template_content("totally different", "memory/MEMORY.md") is False


# ---------------------------------------------------------------------------
# Bundled bootstrap templates
# ---------------------------------------------------------------------------


class TestBundledToolContract:
    def test_tool_contract_balances_general_and_coding_workflows(self):
        from importlib.resources import files as pkg_files

        tpl = pkg_files("jenny") / "templates" / "agent" / "tool_contract.md"
        content = tpl.read_text(encoding="utf-8")

        assert "## General Tool Contract" in content
        assert "Use the narrowest structured tool" in content
        assert "## File and Coding Workflows" in content
        assert "apply_patch" in content
        assert "## Web and External Information" in content
        assert "## Messaging and Media" in content
        assert "pure coding" not in content.lower()
        # "Scheduling and Background Work" è uscita di qui: diceva le stesse tre
        # cose di ``agent/scheduling.md`` ma senza la sua guardia, quindi Dream
        # continuava a leggere di un tool che non ha. V.
        # ``test_no_other_system_prompt_teaches_scheduling``.
        assert "## Scheduling and Background Work" not in content

    def test_tool_contract_is_injected_without_workspace_file(self, tmp_path):
        builder = _builder(tmp_path)
        prompt = builder.build_system_prompt()

        assert "# Tool Usage Notes" in prompt
        assert "## General Tool Contract" in prompt


class TestOutputDestinationRule:
    """Dove finiscono i file prodotti — regola valida in *entrambi* i rami.

    Il doppio caso non è ridondanza: l'orchestratore non scrive file, ma è lui
    a scrivere i prompt dei subagenti con ``spawn``, quindi senza la regola
    detta loro la destinazione sbagliata. Una guardia
    ``{% if not orchestrator %}`` aggiunta per distrazione deve far fallire
    questo test.
    """

    @pytest.mark.parametrize("orchestrator", [True, False])
    def test_absolute_output_path_and_root_ban_in_prompt(self, tmp_path, orchestrator):
        prompt = _builder(tmp_path).build_system_prompt(orchestrator=orchestrator)
        expected = str(tmp_path.resolve() / "output")

        assert expected in prompt
        assert "Never create a new file in the workspace root" in prompt
        # Senza "you may edit but never add to" l'agente smette di aggiornare
        # HEARTBEAT.md, che invece deve continuare a fare.
        assert "you may edit but never add to" in prompt

    def test_rule_sits_at_the_end_of_the_tool_contract(self, tmp_path):
        """In fondo apposta: fra due istruzioni in conflitto vince l'ultima."""
        prompt = _builder(tmp_path).build_system_prompt()
        contract_start = prompt.index("# Tool Usage Notes")
        # Il contratto è l'ultima sezione quando non c'è altro dopo (workspace
        # nudo): niente separatore da cercare, si arriva in fondo.
        contract_end = prompt.find("\n\n---\n\n", contract_start)
        contract = prompt[contract_start:] if contract_end < 0 else prompt[contract_start:contract_end]

        # L'ancora era "## Scheduling and Background Work", uscita dal contratto
        # insieme alla regola che duplicava. "Messaging and Media" la sostituisce
        # nello stesso ruolo: una sezione che sta prima, così l'asserzione parla
        # ancora dell'ordine e non della presenza di un titolo.
        assert contract.index(str(tmp_path.resolve() / "output")) > contract.index(
            "## Messaging and Media"
        )

    def test_download_precedent_is_kept(self, tmp_path):
        """La regola nuova generalizza quella sui download, non la sostituisce."""
        prompt = _builder(tmp_path).build_system_prompt(orchestrator=False)
        assert "Save downloaded files under `downloads/` only, never in the workspace root" in prompt

    def test_output_dir_is_not_created_just_by_building_a_prompt(self, tmp_path):
        _builder(tmp_path).build_system_prompt()
        assert not (tmp_path / "output").exists()


# ---------------------------------------------------------------------------
# _build_user_content
# ---------------------------------------------------------------------------


class TestBuildUserContent:
    def test_no_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", None)
        assert result == "hello"

    def test_empty_media_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [])
        assert result == "hello"

    def test_nonexistent_media_file_returns_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", ["/nonexistent/image.png"])
        assert result == "hello"

    def test_non_image_file_returns_string(self, tmp_path):
        txt = tmp_path / "doc.txt"
        txt.write_text("not an image", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(txt)])
        assert result == "hello"

    def test_valid_image_returns_list(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert result[1]["type"] == "text"
        assert result[1]["text"] == "hello"

    def test_image_meta_includes_path(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        result = builder._build_user_content("hello", [str(png)])
        assert "_meta" in result[0]
        assert "path" in result[0]["_meta"]


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_returns_nonempty_string(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_identity_section(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Android" in result

    def test_identity_includes_platform_policy(self, tmp_path):
        builder = _builder(tmp_path)
        identity = builder._get_identity()
        assert "Android" in identity
        assert "Platform Policy (Android)" in identity
        assert "Chaquopy Python runtime" in identity

    def test_includes_bootstrap_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Be helpful and concise.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "Be helpful and concise." in result

    def test_includes_session_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Previous chat about Python.")
        assert "Previous chat about Python." in result
        assert "[Archived Context Summary]" in result

    def test_sections_separated_by_separator(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Rules.", encoding="utf-8")
        builder = _builder(tmp_path)
        result = builder.build_system_prompt(session_summary="Summary.")
        assert "\n\n---\n\n" in result

    def test_no_bootstrap_no_summary(self, tmp_path):
        builder = _builder(tmp_path)
        result = builder.build_system_prompt()
        assert "## AGENTS.md" not in result
        assert "[Archived Context Summary]" not in result


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_basic_empty_history(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "hello" in str(messages[1]["content"])

    def test_runtime_context_injected(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages([], "hello", channel="internal", chat_id="direct")
        user_msg = str(messages[-1]["content"])
        assert "[Runtime Context" in user_msg
        assert "hello" in user_msg

    def test_session_metadata_injects_active_goal_state(self, tmp_path):
        builder = _builder(tmp_path)
        meta = {
            GOAL_STATE_KEY: {"status": "active", "objective": "Finish docs migration."},
        }
        messages = builder.build_messages(
            [],
            "hi",
            channel="internal",
            chat_id="x",
            session_metadata=meta,
        )
        user_msg = str(messages[-1]["content"])
        assert "Goal (active):" in user_msg
        assert "Finish docs migration." in user_msg

    def test_goal_state_does_not_leak_without_session_metadata(self, tmp_path):
        builder = _builder(tmp_path)
        other_session_meta = {
            GOAL_STATE_KEY: {"status": "active", "objective": "Other chat goal."},
        }

        with_goal = builder.build_messages(
            [],
            "hi",
            channel="websocket",
            chat_id="chat-a",
            session_metadata=other_session_meta,
        )
        without_goal = builder.build_messages(
            [],
            "hi",
            channel="websocket",
            chat_id="chat-b",
            session_metadata={},
        )

        assert "Other chat goal." in str(with_goal[-1]["content"])
        assert "Other chat goal." not in str(without_goal[-1]["content"])
        assert "Goal (active):" not in str(without_goal[-1]["content"])

    def test_current_runtime_lines_are_injected(self, tmp_path):
        builder = _builder(tmp_path)
        messages = builder.build_messages(
            [],
            "please use @zoom tonight",
            current_runtime_lines=[
                "MCP Preset Attachment: @zoom (Zoom; transport=mcp; tool_prefix=mcp_zoom_).",
            ],
        )
        user_msg = str(messages[-1]["content"])

        assert "MCP Preset Attachment: @zoom" in user_msg

    def test_consecutive_same_role_merged(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "user", "content": "previous user message"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 2  # system + merged user
        assert "previous user message" in str(messages[1]["content"])
        assert "new message" in str(messages[1]["content"])

    def test_different_role_appended(self, tmp_path):
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "previous response"}]
        messages = builder.build_messages(history, "new message")
        assert len(messages) == 3  # system + assistant + user

    def test_media_with_history(self, tmp_path):
        png = tmp_path / "img.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        builder = _builder(tmp_path)
        history = [{"role": "assistant", "content": "see this"}]
        messages = builder.build_messages(history, "check image", media=[str(png)])
        user_msg = messages[-1]["content"]
        assert isinstance(user_msg, list)
        assert any(b.get("type") == "image_url" for b in user_msg)


# ---------------------------------------------------------------------------
# Jenny Apps summary in the system prompt
# ---------------------------------------------------------------------------


class TestAppsSummarySection:
    def test_apps_section_present_when_apps_exist(self, tmp_path):
        import json

        app_dir = tmp_path / "apps" / "note"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text(json.dumps({
            "name": "Note", "description": "Note veloci",
            "actions": [
                {"name": "add_note", "description": "Aggiunge", "kind": "storage",
                 "op": "append", "collection": "notes",
                 "params": {"testo": {"type": "string"}}, "required": ["testo"]},
            ],
        }), encoding="utf-8")
        (app_dir / "AGENT.md").write_text("# Note\ncontesto", encoding="utf-8")

        prompt = _builder(tmp_path).build_system_prompt()
        assert "# Jenny Apps" in prompt
        assert "`note_add_note`" in prompt
        assert "apps/note/AGENT.md" in prompt

    def test_apps_section_absent_without_apps(self, tmp_path):
        prompt = _builder(tmp_path).build_system_prompt()
        assert "# Jenny Apps" not in prompt

    def test_broken_app_listed_with_error(self, tmp_path):
        app_dir = tmp_path / "apps" / "rotta"
        app_dir.mkdir(parents=True)
        (app_dir / "app.json").write_text("{nope", encoding="utf-8")
        prompt = _builder(tmp_path).build_system_prompt()
        assert "BROKEN" in prompt
        assert "rotta" in prompt
