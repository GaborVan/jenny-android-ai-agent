"""``docs/reference/configuration.md`` promette «ogni chiave»: qui la promessa si tiene.

La pagina dichiara alla riga 3 di elencare *ogni* chiave che Jenny legge da
``config.json``. Una sezione intera le era sfuggita — ``updates``, cioè le
quattro chiavi del controllo aggiornamenti, che il README presenta come una
connessione in uscita: documentate solo su una pagina per chi contribuisce.

Si pinna l'inclusione, non un conteggio in prosa: un numero scritto a mano va
riscritto a ogni campo, e invecchia in silenzio (è così che «dieci permessi» è
sopravvissuto a cinque permessi nuovi in ``README.md``).

Il controllo è volutamente al livello delle sezioni di primo livello e non di
ogni campo nidificato: la pagina raggruppa alcuni campi in prosa invece che in
tabella, e un test troppo fine avrebbe costretto a scrivere per il test.
"""

from __future__ import annotations

from pathlib import Path

from jenny.config.schema import Config

DOC = Path(__file__).resolve().parents[2] / "docs" / "reference" / "configuration.md"


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(word.title() for word in rest)


def test_every_top_level_config_section_is_documented() -> None:
    text = DOC.read_text("utf-8")
    missing = sorted(
        name
        for name in Config.model_fields
        if name not in text and _camel(name) not in text
    )

    assert not missing, (
        f"sezioni di config.json assenti da {DOC.name}: {missing}. "
        "La pagina dichiara di elencare ogni chiave che Jenny legge."
    )
