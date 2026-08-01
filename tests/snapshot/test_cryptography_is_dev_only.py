"""Invariante di packaging: ``cryptography`` non è una dipendenza runtime.

Su Android il backend crypto è ``javax.crypto`` (Chaquopy); pyca/``cryptography``
ha binding nativi e non deve MAI entrare nei requirements Android. L'unico uso
consentito è l'import lazy (dentro funzione) in ``crypto_backends/dev.py``:
un import a livello modulo in un punto qualunque di ``jenny/`` farebbe
esplodere l'app al primo import sul device.
"""

from __future__ import annotations

import re
from pathlib import Path

JENNY_DIR = Path(__file__).resolve().parents[2] / "jenny"

# Import a colonna zero = import a livello modulo (quelli lazy sono indentati).
_TOP_LEVEL_IMPORT = re.compile(r"^(import cryptography|from cryptography[. ])")


def test_no_top_level_cryptography_import_in_runtime() -> None:
    offenders: list[str] = []
    for path in JENNY_DIR.rglob("*.py"):
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if _TOP_LEVEL_IMPORT.match(line):
                offenders.append(f"{path.relative_to(JENNY_DIR.parent)}:{lineno}")
    assert not offenders, (
        "import di 'cryptography' a livello modulo nel runtime (vietato: su "
        f"Android esiste solo javax.crypto): {offenders}"
    )


def test_pyproject_does_not_depend_on_cryptography() -> None:
    """La dipendenza non deve comparire nemmeno tra i requirements del package."""
    pyproject = (JENNY_DIR.parent / "pyproject.toml").read_text("utf-8")
    dependencies = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "cryptography" not in dependencies
