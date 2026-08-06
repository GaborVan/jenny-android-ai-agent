"""Invariante di packaging: ``asyncssh`` non è una dipendenza runtime.

Su Android il client SSH è jsch, via bridge nativo. ``asyncssh`` tira dentro
``cryptography``, che ha binding nativi: farla entrare nei requirements Android
significherebbe imbarcare nell'APK proprio la dipendenza che abbiamo deciso di
non avere, e per giunta bloccata alla wheel 42.0.8 che Chaquopy pubblica.

L'unico uso consentito è l'import lazy (dentro funzione) in
``ssh_backends/dev.py``: un import a livello modulo in un punto qualunque di
``jenny/`` farebbe esplodere l'app al primo import sul device.

Gemello di ``tests/snapshot/test_cryptography_is_dev_only.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

# tests/agent/tools/<questo file> → tre livelli fino a tests/, quattro alla radice.
REPO_ROOT = Path(__file__).resolve().parents[3]
JENNY_DIR = REPO_ROOT / "jenny"

# Import a colonna zero = import a livello modulo (quelli lazy sono indentati).
_TOP_LEVEL_IMPORT = re.compile(r"^(import asyncssh|from asyncssh[. ])")


def test_no_top_level_asyncssh_import_in_runtime() -> None:
    offenders: list[str] = []
    for path in JENNY_DIR.rglob("*.py"):
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if _TOP_LEVEL_IMPORT.match(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "import di 'asyncssh' a livello modulo nel runtime (vietato: su Android "
        f"il client SSH e jsch via bridge nativo): {offenders}"
    )


def test_pyproject_does_not_depend_on_asyncssh() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
    dependencies = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "asyncssh" not in dependencies


def test_android_requirements_do_not_include_asyncssh() -> None:
    """Il controllo che conta davvero: e questo il file che Chaquopy installa."""
    for name in ("requirements-android.txt", "requirements-android.lock.txt"):
        text = (REPO_ROOT / name).read_text("utf-8")
        declared = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not any("asyncssh" in line for line in declared), (
            f"asyncssh dichiarato in {name}: finirebbe nell'APK"
        )
        assert not any("cryptography" in line for line in declared), (
            f"cryptography dichiarata in {name}: e la dipendenza nativa che "
            "la scelta di jsch serviva a evitare"
        )
