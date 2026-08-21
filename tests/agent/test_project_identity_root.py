"""Dentro un progetto Jenny sa ancora chi è, e chi sei tu.

Il prompt di un turno viene costruito sulla radice dello *scope* — la cartella
del progetto, quando la sessione ne ha una legata. Finché i file di bootstrap
venivano tutti da lì, legare uno scope faceva cercare `SOUL.md` e `USER.md`
dentro la wiki, dove non ci sono; e chi li carica salta i file assenti con un
`continue`. Risultato: Jenny senza personalità e senza niente di quel che sa
dell'utente, **senza un errore e senza una riga di log**.

Il rimedio è spaccare una radice in due — identità dall'installazione,
istruzioni dalla cartella legata — e questi test lo tengono fermo dai due lati:
che l'identità arrivi, e che le istruzioni del progetto siano le sue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.context import ContextBuilder

pytestmark = pytest.mark.usefixtures("_configure_jenny_workspace")

_SOUL = "# Chi sono\n\nSono Jenny e parlo come parlo io.\n"
_USER = "# Utente\n\n- Vive a Bologna\n- Preferisce l'italiano\n"
_INSTALL_AGENTS = "# Istruzioni\n\nQueste sono le istruzioni della radice.\n"
_PROJECT_AGENTS = "# Istruzioni\n\nQui si scrive una wiki su Patreon.\n"


@pytest.fixture
def install_root(tmp_path: Path) -> Path:
    """La radice dell'installazione, con identità e istruzioni sue."""
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    (root / "SOUL.md").write_text(_SOUL, encoding="utf-8")
    (root / "USER.md").write_text(_USER, encoding="utf-8")
    (root / "AGENTS.md").write_text(_INSTALL_AGENTS, encoding="utf-8")
    return root


@pytest.fixture
def project_root(install_root: Path) -> Path:
    """Una wiki legata: ha le proprie istruzioni e nient'altro."""
    root = install_root / "wikis" / "patreon"
    root.mkdir(parents=True)
    (root / "AGENTS.md").write_text(_PROJECT_AGENTS, encoding="utf-8")
    return root


class TestDentroUnProgetto:
    def test_jenny_e_ancora_se_stessa(self, install_root, project_root):
        """Il test che fallisce sul codice di prima: senza questo, il prompt di
        un turno legato non conteneva nessuna delle due righe."""
        builder = ContextBuilder(install_root)

        prompt = builder.build_system_prompt(workspace=project_root)

        assert "parlo come parlo io" in prompt
        assert "Vive a Bologna" in prompt

    def test_le_istruzioni_sono_quelle_del_progetto(self, install_root, project_root):
        builder = ContextBuilder(install_root)

        prompt = builder.build_system_prompt(workspace=project_root)

        assert "si scrive una wiki su Patreon" in prompt
        assert "istruzioni della radice" not in prompt

    def test_un_progetto_senza_istruzioni_proprie_non_eredita_quelle_di_casa(
        self, install_root, project_root
    ):
        """Meglio nessuna istruzione che quelle di un altro posto.

        `AGENTS.md` descrive *questo* posto di lavoro: farlo ricadere sulla
        radice direbbe al progetto le regole del workspace personale.
        """
        (project_root / "AGENTS.md").unlink()
        builder = ContextBuilder(install_root)

        prompt = builder.build_system_prompt(workspace=project_root)

        assert "istruzioni della radice" not in prompt
        # …ma l'identità c'è comunque.
        assert "parlo come parlo io" in prompt

    def test_la_memoria_lunga_resta_quella_dell_installazione(
        self, install_root, project_root
    ):
        """Non passa dai file di bootstrap ma da `MemoryStore`, costruito una
        volta sulla radice. Era giusto per caso: adesso è tenuto fermo."""
        memory = install_root / "memory"
        memory.mkdir(parents=True, exist_ok=True)
        (memory / "MEMORY.md").write_text("- Il telefono è un Titan 2\n", encoding="utf-8")
        builder = ContextBuilder(install_root)

        prompt = builder.build_system_prompt(workspace=project_root)

        assert "Titan 2" in prompt

    def test_la_cartella_di_lavoro_annunciata_e_quella_del_progetto(
        self, install_root, project_root
    ):
        """L'identità non segue lo scope, il posto di lavoro sì: sono due cose
        diverse, e il prompt deve dire dove si lavora."""
        builder = ContextBuilder(install_root)

        prompt = builder.build_system_prompt(workspace=project_root)

        assert str(project_root) in prompt


class TestFuoriDaUnProgetto:
    def test_senza_scope_non_cambia_niente(self, install_root):
        """Le due radici coincidono: stesso prompt di prima, byte per byte."""
        builder = ContextBuilder(install_root)

        legacy = builder.build_system_prompt(workspace=install_root)
        implicit = builder.build_system_prompt()

        assert legacy == implicit
        assert "istruzioni della radice" in implicit
        assert "parlo come parlo io" in implicit
