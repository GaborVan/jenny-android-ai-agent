"""Le taglie della mascotte sono scritte in due posti: devono coincidere.

``shared/mascot.js`` è la fonte di verità a runtime, ma ``bootstrap.js`` gira
prima di qualsiasi modulo ES (non può importare) e riscrive ``--jenny-size``
per evitare che Jenny compaia media e poi si ridimensioni. Se le due tabelle
divergono, il flash torna — e solo per chi non usa la taglia di default, cioè
esattamente il caso che quel codice esiste per coprire.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_ASSETS = Path(__file__).resolve().parents[2] / "jenny" / "templates" / "ui" / "assets"


def _sizes_from(source: str, pattern: str) -> dict[str, int]:
    match = re.search(pattern, source)
    assert match, f"tabella delle taglie non trovata con {pattern!r}"
    return {
        key: int(value)
        for key, value in re.findall(r"(\w+):\s*'?(\d+)", match.group(1))
    }


def test_bootstrap_and_mascot_module_agree_on_sizes():
    module = _sizes_from(
        (UI_ASSETS / "shared" / "mascot.js").read_text("utf-8"),
        r"MASCOT_SIZES\s*=\s*\{([^}]*)\}",
    )
    bootstrap = _sizes_from(
        (UI_ASSETS / "bootstrap.js").read_text("utf-8"),
        r"mascotSizes\s*=\s*\{([^}]*)\}",
    )

    assert module, "MASCOT_SIZES è vuota o illeggibile"
    assert module == bootstrap, (
        "le taglie della mascotte divergono fra shared/mascot.js e bootstrap.js: "
        f"{module} vs {bootstrap}"
    )


def test_default_size_matches_the_css_token():
    """Il default CSS copre 'md': se cambia una delle due, cambiano entrambe."""
    module = _sizes_from(
        (UI_ASSETS / "shared" / "mascot.js").read_text("utf-8"),
        r"MASCOT_SIZES\s*=\s*\{([^}]*)\}",
    )
    css = (UI_ASSETS / "mobile-style.css").read_text("utf-8")
    token = re.search(r"--jenny-size:\s*(\d+)px", css)
    assert token, "--jenny-size non è più definita in :root"
    assert int(token.group(1)) == module["md"]
