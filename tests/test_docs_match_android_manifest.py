"""Ogni permesso dichiarato dal manifest deve comparire nella pagina che li elenca.

``docs/reference/android-permissions.md`` è la pagina che il README presenta come
«ogni connessione e permesso»: se il manifest ne dichiara uno in più, quella
promessa diventa falsa in silenzio. È già successo con i tre permessi del
percorso di aggiornamento — `REQUEST_DELETE_PACKAGES`,
`REQUEST_INSTALL_PACKAGES`, `UPDATE_PACKAGES_WITHOUT_USER_ACTION` — che non
comparivano né lì, né nel README, né in ``SECURITY.md``, mentre il README
contava «dieci permessi» su quindici dichiarati.

Il conteggio non si pinna come numero: un numero in prosa invecchia e va
riscritto a ogni permesso. Si pinna l'inclusione, che è l'invariante vera.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "android/app/src/main/AndroidManifest.xml"
PERMISSIONS_DOC = REPO_ROOT / "docs/reference/android-permissions.md"


def _declared_permissions() -> set[str]:
    if not MANIFEST.is_file():
        pytest.skip("manifest Android non presente in questo checkout")
    text = MANIFEST.read_text("utf-8")
    return set(re.findall(r'android:name="android\.permission\.([A-Z_]+)"', text))


def test_every_declared_permission_is_documented() -> None:
    documented = PERMISSIONS_DOC.read_text("utf-8")
    undocumented = sorted(p for p in _declared_permissions() if p not in documented)

    assert not undocumented, (
        "permessi dichiarati dal manifest e assenti da "
        f"docs/reference/android-permissions.md: {undocumented}. "
        "La pagina è quella che il README presenta come «ogni permesso»."
    )


def test_the_doc_does_not_invent_permissions() -> None:
    """Direzione speculare: un permesso documentato e non più dichiarato inganna al pari.

    Chi lo legge crede di dover valutare un accesso che l'app non chiede — e chi
    lo rimuove dal manifest non ha modo di accorgersi che la pagina lo promette
    ancora.
    """
    declared = _declared_permissions()
    doc = PERMISSIONS_DOC.read_text("utf-8")
    # Solo i nomi in backtick nella colonna di sinistra: la prosa cita anche
    # permessi *deliberatamente non richiesti* (CAMERA, QUERY_ALL_PACKAGES…),
    # che è informazione voluta, non deriva.
    claimed = set(re.findall(r"^\| `([A-Z_]+)`", doc, re.MULTILINE))
    for pair in re.findall(r"^\| `([A-Z_]+)` / `([A-Z_]+)`", doc, re.MULTILINE):
        claimed.update(pair)

    stale = sorted(claimed - declared)
    assert not stale, f"permessi elencati nella pagina ma non più nel manifest: {stale}"
