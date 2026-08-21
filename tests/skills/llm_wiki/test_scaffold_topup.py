"""Test di regressione per `scaffold.py`: crea quel che manca, non tocca il resto.

Prima di questa modifica `_write()` era un `open(full, "w")` secco, quindi
rilanciare lo scaffold su una wiki esistente per "aggiungere quel che manca" ne
azzerava `wiki/index.md` e riscriveva il log del giorno. La fixture centrale qui
e' modellata sulla deriva misurata sul telefono tra `main/` e `patreon-creator/`:
la seconda non ha `CLAUDE.md`, ne' `audit/`, ne' `outputs/`, ma ha contenuto vero
in `wiki/index.md` e nel log di oggi.

Gli script della skill non fanno parte del package `jenny` importabile, quindi la
dir `scripts/` viene aggiunta a `sys.path`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3] / "jenny" / "skills" / "llm-wiki" / "scripts"
)


@pytest.fixture(scope="module")
def scaffold():
    """Carica `scaffold.py` come modulo (la dir non e' un package importabile)."""
    sys.path.insert(0, str(_SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("scaffold", _SCRIPTS_DIR / "scaffold.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digests(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _today_log_name() -> str:
    return f"{date.today():%Y%m%d}.md"


@pytest.fixture
def drifted_wiki(tmp_path: Path) -> Path:
    """Una wiki vera e incompleta: manca CLAUDE.md, audit/, outputs/."""
    root = tmp_path / "wikis" / "patreon-creator"
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "raw" / "notes").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    (root / "wiki" / "index.md").write_text(
        "# Index — Patreon\n\n## Concepts\n- [[concepts/Pricing]]\n", encoding="utf-8"
    )
    (root / "research-plan.md").write_text("# Plan\n\nscritto a mano.\n", encoding="utf-8")
    (root / "log" / _today_log_name()).write_text(
        f"# {date.today().isoformat()}\n\n## [09:12] ingest | 3 fonti\n", encoding="utf-8"
    )
    return root


# ── il top-up ────────────────────────────────────────────────────────────────


def test_topup_crea_i_buchi_e_lascia_intatto_il_resto(scaffold, drifted_wiki, capsys):
    before = _digests(drifted_wiki)

    created = scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    # Quel che mancava ora c'e'.
    assert (drifted_wiki / "CLAUDE.md").is_file()
    assert (drifted_wiki / "audit" / "resolved").is_dir()
    assert (drifted_wiki / "outputs" / "queries").is_dir()
    assert "CLAUDE.md" in created

    # E niente di quel che c'era prima e' cambiato di un byte. Questo assert sta
    # tra il top-up e lo svuotamento di una wiki vera.
    after = _digests(drifted_wiki)
    assert {k: after[k] for k in before} == before


def test_topup_non_appende_al_log_di_oggi(scaffold, drifted_wiki, capsys):
    log = drifted_wiki / "log" / _today_log_name()
    before = log.read_text(encoding="utf-8")

    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    # La skill chiede di loggare ogni operazione, ma un top-up non tocca un file
    # che esiste: il report va su stdout. Se un giorno si decide di appendere,
    # va deciso, non scoperto rompendo questo test.
    assert log.read_text(encoding="utf-8") == before


def test_topup_scrive_il_log_di_oggi_se_manca_ed_elenca_quel_che_ha_aggiunto(
    scaffold, drifted_wiki, capsys
):
    (drifted_wiki / "log" / _today_log_name()).unlink()

    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    capsys.readouterr()

    log = (drifted_wiki / "log" / _today_log_name()).read_text(encoding="utf-8")
    assert log.startswith(f"# {date.today().isoformat()}\n")
    assert "scaffold | Topped up Patreon Creator scaffolding" in log
    assert "- Created CLAUDE.md" in log
    # Non annuncia di aver creato quel che c'era gia'.
    assert "wiki/index.md" not in log


def test_un_secondo_run_non_crea_nulla(scaffold, drifted_wiki, capsys):
    scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    snapshot = _digests(drifted_wiki)

    created = scaffold.scaffold(str(drifted_wiki), "Patreon Creator")
    out = capsys.readouterr().out

    assert created == []
    assert _digests(drifted_wiki) == snapshot
    assert "Nothing to add" in out


def test_write_non_sovrascrive_un_file_esistente(scaffold, tmp_path: Path):
    """Il confine sta in `_write`, in un punto solo: pin diretto."""
    (tmp_path / "già.md").write_text("mio", encoding="utf-8")

    assert scaffold._write(str(tmp_path), "già.md", "sovrascritto") is False
    assert (tmp_path / "già.md").read_text(encoding="utf-8") == "mio"

    assert scaffold._write(str(tmp_path), "nuovo/file.md", "creato") is True
    assert (tmp_path / "nuovo" / "file.md").read_text(encoding="utf-8") == "creato"


# ── la wiki nuova, che deve restare quella di prima ──────────────────────────


def test_una_wiki_nuova_nasce_completa(scaffold, tmp_path: Path, capsys):
    root = tmp_path / "wikis" / "nuova"

    created = scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    for rel in (
        "CLAUDE.md",
        "wiki/index.md",
        f"log/{_today_log_name()}",
        "audit/.gitkeep",
        "audit/resolved/.gitkeep",
    ):
        assert (root / rel).is_file(), rel
    for rel in ("raw/articles", "raw/papers", "raw/notes", "raw/refs", "outputs/queries"):
        assert (root / rel).is_dir(), rel
    assert (root.parent / "_index.md").is_file()
    assert "CLAUDE.md" in created


def test_la_voce_di_log_di_una_wiki_nuova_e_quella_di_prima(scaffold, tmp_path: Path, capsys):
    """La forma del log fresco non cambia: `lint_wiki` ne verifica l'H1 e la
    skill dice di ricopiarla a mano quando appende."""
    root = tmp_path / "wikis" / "nuova"

    scaffold.scaffold(str(root), "Nuova")
    capsys.readouterr()

    log = (root / "log" / _today_log_name()).read_text(encoding="utf-8")
    assert log.startswith(f"# {date.today().isoformat()}\n\n## [")
    assert "scaffold | Initialized Nuova knowledge base\n" in log
    assert log.endswith(
        "- Created directory tree (raw/, wiki/, log/, audit/, outputs/)\n"
        "- Created CLAUDE.md schema template\n"
        "- Created wiki/index.md category skeleton\n"
    )
