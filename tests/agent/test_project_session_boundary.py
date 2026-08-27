"""La terza categoria di sessione, e il confine che la tiene fuori dal diario.

Fino a ieri le sessioni erano due — lavoro interno e conversazione con l'utente —
e "chi non e' interno" bastava come definizione di "personale". Con le sessioni
progetto ne esiste una terza che e' conversazione con l'utente e **non** deve
alimentare la memoria di lungo periodo, quindi quella definizione diventa
silenziosamente sbagliata: una chiave ``project:`` non e' interna, e per la
vecchia regola risultava percio' personale, cioe' sarebbe finita in ``MEMORY.md``.

Questi test arrivano **prima** che qualcosa produca una chiave di progetto, e
usano chiavi sintetiche di proposito: il confine deve essere una proprieta della
chiave e non della UI che la sceglie, altrimenti legare la cartella alla sessione
sarebbe un cambiamento che accende una falla invece di una funzione.

Le due meta vanno lette insieme, e l'asimmetria con le sessioni interne e'
voluta: per un job cron scrivere in quella coda e' la cura di un difetto vero
(rilegge i propri run passati), per un progetto e' una perdita. Chi tocca uno dei
due rami legga ``test_dream_history_boundary.py``, che tiene ferma l'altra meta.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from jenny.agent.context import ContextBuilder
from jenny.agent.memory import Consolidator, MemoryStore
from jenny.session.keys import (
    is_internal_session_key,
    is_personal_session_key,
    is_project_session_key,
    normalize_user_session_key,
    project_session_key,
    session_kind,
)
from jenny.session.manager import Session

PERSONAL = "unified:default"
PROJECT = "project:patreon"
OTHER_PROJECT = "project:etf-finance"
CRON = "cron:job-1"
OTHER_CRON = "cron:job-2"


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path)


def _inject_raw(store: MemoryStore, content: str, session_key: str) -> None:
    """Scrive una voce nella coda scavalcando il gate.

    Serve perche' il gate sulla scrittura rende impossibile creare una voce di
    progetto passando dall'API: senza questo, i test della *lettura* verificherebbero
    solo che una cosa che non puo' esistere non si vede. Una voce del genere puo'
    arrivare da una versione precedente del codice o da un file modificato a mano,
    ed e' esattamente il caso che il secondo giro di chiave deve coprire.
    """
    cursor = store._next_cursor()
    record = {
        "cursor": cursor,
        "timestamp": "2026-08-21 12:00",
        "content": content,
        "session_key": session_key,
    }
    with open(store.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── la classificazione ───────────────────────────────────────────────────────


class TestLaClassificazioneETernaria:
    @pytest.mark.parametrize(
        ("key", "kind"),
        [
            (PERSONAL, "personal"),
            ("websocket:qualunque", "personal"),
            (PROJECT, "project"),
            (OTHER_PROJECT, "project"),
            (CRON, "internal"),
            ("dream:20260821-1200", "internal"),
            ("subagent:abc", "internal"),
            ("atlas:20260821", "internal"),
            ("internal:direct", "internal"),
            ("heartbeat", "internal"),
        ],
    )
    def test_ogni_chiave_cade_in_una_categoria_sola(self, key, kind):
        assert session_kind(key) == kind
        # E i tre predicati sono d'accordo con lei: sono la stessa funzione.
        assert is_personal_session_key(key) is (kind == "personal")
        assert is_project_session_key(key) is (kind == "project")
        assert is_internal_session_key(key) is (kind == "internal")

    @pytest.mark.parametrize(
        "key",
        ["api:vision", "system", "review:20260823", "qualcosa-di-nuovo", "websocketx:y"],
    )
    def test_un_prefisso_non_registrato_non_e_personale(self, key):
        """**T4.10.** Il residuo cade nel bucket prudente, non nel diario.

        Prima di oggi ``session_kind`` era: interna se il prefisso e' registrato,
        progetto se e' ``project:``, **personale tutto il resto**. Cioe' un kind
        nuovo il cui prefisso qualcuno si dimenticasse di registrare finiva nel
        solo bucket che Dream consuma (``MemoryStore.build_dream_prompt`` filtra
        su ``is_personal_session_key``): il suo contenuto entrava in
        ``MEMORY.md``, senza che nessuna riga dica da dove viene.

        Ora la terza categoria e' una whitelist vera e il residuo e' ``internal``,
        che e' il bucket "lavoro del sistema": non alimenta la memoria di lungo
        periodo e non compare negli elenchi user-facing. Un kind legittimo nuovo
        classificato cosi' si rompe **a vista** al primo giro; uno classificato
        personale non si rompe affatto, scrive nel diario e non lo si scopre.
        """
        assert not is_personal_session_key(key)
        assert session_kind(key) == "internal"

    def test_la_whitelist_personale_e_la_conversazione_e_le_chiavi_di_prima(self):
        """Chi *puo'* alimentare ``MEMORY.md``: la sessione unica e le legacy.

        Le ``<canale>:<chat_id>`` non sono piu' sessioni, ma stanno scritte nelle
        voci di ``history.jsonl`` di prima della sessione unica ed erano la
        conversazione con l'utente: tenerle fuori dalla whitelist renderebbe
        invisibile a Dream la storia gia' sul disco.
        """
        assert is_personal_session_key(PERSONAL)
        assert is_personal_session_key("websocket:qualunque")
        assert is_personal_session_key("telegram:12345")

    def test_una_chiave_non_classificata_lo_dice(self):
        """Fail-closed **e** ad alta voce: il silenzio era metà del difetto.

        Il rifiuto non e' un'eccezione di proposito: ``session_kind`` gira anche
        sul campo ``session_key`` delle voci di ``history.jsonl``, scritte da
        versioni precedenti e modificabili a mano, e un'eccezione la' farebbe
        cadere Dream e l'autocompaction su una riga vecchia. Resta il log.
        """
        from jenny.session import keys as keys_mod

        keys_mod._UNCLASSIFIED_WARNED.discard("zzsconosciuto")
        messages: list[str] = []
        sink = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
        try:
            assert session_kind("zzsconosciuto:1") == "internal"
        finally:
            logger.remove(sink)
        assert any("zzsconosciuto:1" in m for m in messages), messages

    def test_un_progetto_non_e_ne_interno_ne_personale(self):
        """Il difetto che questa categoria esiste per chiudere.

        Prima, ``is_personal_session_key`` era ``not is_internal_session_key``, e
        una chiave di progetto passava quel filtro: la conversazione di un
        progetto sarebbe entrata nel diario personale.
        """
        assert not is_internal_session_key(PROJECT)
        assert not is_personal_session_key(PROJECT)

    def test_la_chiave_si_compone_da_un_punto_solo(self):
        assert project_session_key("patreon") == PROJECT
        assert is_project_session_key(project_session_key("qualunque-cosa"))

    def test_la_migrazione_delle_chiavi_legacy_non_tocca_un_progetto(self):
        """``<canale>:<chat_id>`` collassa sulla conversazione unica, un progetto no.

        E' il motivo per cui quell'elenco e' chiuso e non un pattern: collassare
        ``project:<id>`` sulla chat personale ci farebbe girare dentro il lavoro
        di un progetto.
        """
        assert normalize_user_session_key("websocket:default") == PERSONAL
        assert normalize_user_session_key(PROJECT) == PROJECT


# ── la scrittura ─────────────────────────────────────────────────────────────


class TestLaScritturaEChiusa:
    def test_un_progetto_non_scrive_nella_coda(self, store):
        store.append_history("fatto personale", session_key=PERSONAL)
        before = store.history_file.read_bytes()

        cursor = store.append_history("cosa detta dentro il progetto", session_key=PROJECT)

        assert cursor == 0, "0 non e' un cursore valido: segnala che non ha scritto"
        assert store.history_file.read_bytes() == before

    def test_il_cursore_non_avanza(self, store):
        """Il cursore e' la contabilita della conversazione personale.

        Se avanzasse per un turno di progetto, Dream si troverebbe la finestra
        spostata oltre voci personali che non ha ancora letto: le perderebbe
        senza che niente lo dica.
        """
        personal_cursor = store.append_history("fatto personale", session_key=PERSONAL)

        store.append_history("cosa di progetto", session_key=PROJECT)

        assert store._next_cursor() == personal_cursor + 1

    def test_anche_il_dump_grezzo_e_chiuso(self, store):
        """``raw_archive`` e' il ramo di fallback quando la chiamata LLM fallisce.

        Passa dallo stesso imbuto, ed e' la ragione per cui il gate sta in
        ``append_history`` e non in ``Consolidator.archive``: chiudere solo il
        ramo felice avrebbe lasciato aperto quello che scatta quando le cose
        vanno male.
        """
        before = store.history_file.read_bytes() if store.history_file.exists() else b""

        store.raw_archive(
            [{"role": "user", "content": "una cosa personale detta in un progetto"}],
            session_key=PROJECT,
        )

        after = store.history_file.read_bytes() if store.history_file.exists() else b""
        assert after == before

    def test_una_sessione_interna_scrive_ancora(self, store):
        """L'asimmetria, fissata: non e' una dimenticanza da "sistemare".

        Un job cron rilegge le proprie voci in questa coda — e' cosi che si
        ricorda dei run passati. Chiudere anche questa scrittura romperebbe la
        cura dell'amnesia dell'heartbeat.
        """
        cursor = store.append_history("il job ha girato", session_key=CRON)

        assert cursor > 0
        assert "il job ha girato" in store.history_file.read_text(encoding="utf-8")

    def test_una_voce_senza_chiave_scrive_ancora(self, store):
        """Il campo e' opzionale: il gate non deve trasformare l'assenza in un rifiuto."""
        assert store.append_history("voce senza attribuzione") > 0


# ── la lettura ───────────────────────────────────────────────────────────────


class TestLaLetturaEUnAssenza:
    def test_un_progetto_non_legge_la_coda(self, store):
        store.append_history("fatto personale", session_key=PERSONAL)
        store.append_history("voce di un job", session_key=CRON)

        assert store.read_recent_history_for_prompt(0, session_key=PROJECT) == []

    def test_nemmeno_le_proprie_voci(self, store):
        """Non "niente di altrui": proprio niente.

        Un progetto non condivide la finestra — il cursore di Dream — quindi non
        ha senso che ci legga dentro nemmeno le voci che porterebbero la sua
        chiave. La continuita di un progetto vive nella sua sessione e nei suoi
        file.
        """
        _inject_raw(store, "vecchia voce del progetto", PROJECT)

        assert store.read_recent_history_for_prompt(0, session_key=PROJECT) == []

    def test_una_sessione_personale_non_vede_una_voce_di_progetto(self, store):
        """Il secondo giro di chiave: la whitelist, non la negazione.

        Con la vecchia condizione (``not e' interna``) una voce di progetto
        finita nel file — da una versione precedente, o scritta a mano — sarebbe
        entrata nel prompt di *ogni* sessione.
        """
        store.append_history("fatto personale", session_key=PERSONAL)
        _inject_raw(store, "roba del progetto", PROJECT)

        entries = store.read_recent_history_for_prompt(0, session_key=PERSONAL)

        contents = [e["content"] for e in entries]
        assert "fatto personale" in contents
        assert "roba del progetto" not in contents

    def test_una_sessione_interna_non_vede_una_voce_di_progetto(self, store):
        store.append_history("voce mia", session_key=CRON)
        _inject_raw(store, "roba del progetto", PROJECT)

        contents = [
            e["content"] for e in store.read_recent_history_for_prompt(0, session_key=CRON)
        ]
        assert "voce mia" in contents
        assert "roba del progetto" not in contents

    def test_la_regola_ternaria_di_una_sessione_interna_resta(self, store):
        """Le proprie voci *piu* la conversazione personale, e non quelle di un altro job."""
        store.append_history("conversazione personale", session_key=PERSONAL)
        store.append_history("voce mia", session_key=CRON)
        store.append_history("voce di un altro job", session_key=OTHER_CRON)

        contents = [
            e["content"] for e in store.read_recent_history_for_prompt(0, session_key=CRON)
        ]
        assert "conversazione personale" in contents
        assert "voce mia" in contents
        assert "voce di un altro job" not in contents

    def test_senza_chiave_si_legge_tutto(self, store):
        """``session_key=None`` e' l'accesso non filtrato di chi non e' un turno."""
        store.append_history("personale", session_key=PERSONAL)
        store.append_history("interna", session_key=CRON)

        assert len(store.read_recent_history_for_prompt(0, session_key=None)) == 2


# ── Dream ────────────────────────────────────────────────────────────────────


class TestDreamNonVedeUnProgetto:
    def test_una_voce_di_progetto_non_entra_nel_prompt_di_dream(self, store):
        store.append_history("- [durable] fatto personale", session_key=PERSONAL)
        _inject_raw(store, "- [durable] fatto detto in un progetto", PROJECT)

        result = store.build_dream_prompt()
        assert result is not None
        batch = MemoryStore.dream_prompt_history(result[0])

        assert "fatto personale" in batch
        assert "detto in un progetto" not in batch

    def test_una_coda_di_sole_voci_di_progetto_non_fa_partire_dream(self, store):
        _inject_raw(store, "solo roba di progetto", PROJECT)

        assert store.build_dream_prompt() is None


# ── il prompt di un turno ────────────────────────────────────────────────────


class TestIlPromptDiUnProgetto:
    pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

    def test_non_ha_nessun_blocco_recent_history(self, tmp_path):
        """Non un blocco filtrato: nessun blocco.

        E' la forma che il piano chiede, e la ragione e' che un'assenza non si
        puo' sbagliare mentre un filtro si.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        builder = ContextBuilder(workspace)
        builder.memory.append_history("storia personale", session_key=PERSONAL)
        builder.memory.append_history("storia di un job", session_key=CRON)

        prompt = builder.build_system_prompt(session_key=PROJECT)

        assert "# Recent History" not in prompt
        assert "storia personale" not in prompt
        assert "storia di un job" not in prompt

    def test_la_conversazione_personale_ce_l_ha_ancora(self, tmp_path):
        """Controllo: il blocco non e' scomparso per tutti."""
        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True)
        builder = ContextBuilder(workspace)
        builder.memory.append_history("storia personale", session_key=PERSONAL)

        prompt = builder.build_system_prompt(session_key=PERSONAL)

        assert "# Recent History" in prompt
        assert "storia personale" in prompt

# ── la compattazione, che deve continuare a funzionare ───────────────────────


@pytest.fixture
def consolidator(store):
    """Consolidator con un provider finto: il riassunto e' una costante."""
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(
        return_value=MagicMock(content="riassunto della conversazione", finish_reason="stop")
    )
    return Consolidator(
        store=store,
        provider=provider,
        model="test-model",
        sessions=MagicMock(save=MagicMock()),
        context_window_tokens=100_000,
        build_messages=MagicMock(return_value=[]),
        get_tool_definitions=MagicMock(return_value=[]),
        max_completion_tokens=100,
    )


class TestLaCompattazioneDiUnProgettoFunziona:
    """La meta che si rompe se si chiude la cosa sbagliata.

    Un progetto **si compatta** — e' il modello di Claude Code, una
    conversazione sola che quando diventa lunga viene riassunta e va avanti. Quel
    che non deve fare e' mandare il riassunto nella coda del diario. Se qualcuno
    chiudesse la compattazione invece della scrittura, un progetto riaperto dopo
    mesi non avrebbe piu niente in mano, e nessun test lo direbbe: sarebbero solo
    dei messaggi in meno.
    """

    async def test_il_riassunto_viene_prodotto_ma_la_coda_non_lo_riceve(
        self, consolidator, store
    ):
        before = store.history_file.read_bytes() if store.history_file.exists() else b""

        summary = await consolidator.archive(
            [{"role": "user", "content": "una lunga conversazione sul progetto"}],
            session_key=PROJECT,
        )

        assert summary == "riassunto della conversazione"
        after = store.history_file.read_bytes() if store.history_file.exists() else b""
        assert after == before

    async def test_il_riassunto_finisce_nei_metadati_della_sessione(
        self, consolidator, store
    ):
        """E' da lì che il turno dopo lo rilegge, non dalla coda.

        Per questo chiudere la scrittura non costa niente alla compattazione:
        ``_last_summary`` e la sessione sono la stessa cosa, e la coda non c'entra.
        """
        session = Session(key=PROJECT)

        summary = await consolidator.archive(
            [{"role": "user", "content": "conversazione"}], session_key=session.key
        )
        consolidator._persist_last_summary(session, summary)

        assert session.metadata["_last_summary"]["text"] == "riassunto della conversazione"

    async def test_per_la_conversazione_personale_la_coda_lo_riceve(
        self, consolidator, store
    ):
        """Controllo: il consolidamento non ha smesso di scrivere per tutti."""
        await consolidator.archive(
            [{"role": "user", "content": "una conversazione"}], session_key=PERSONAL
        )

        assert "riassunto della conversazione" in store.history_file.read_text("utf-8")
