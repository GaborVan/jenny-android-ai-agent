"""L'intestazione di un file d'archivio, e il caso in cui non si chiude.

``_parse_archived`` è tollerante di proposito: la cartella ``memory/archive/`` è
visibile nel file browser della WebUI, quindi verrà aperta e semplificata a mano,
e un file senza intestazione deve restare leggibile — il suo corpo è tutto il
testo. Quella tolleranza ha però un confine, e il confine è il caso in cui il
``---`` di apertura non ha un ``---`` di chiusura: il file è **troncato**, e le
righe che si vedono sono metadati, non il fatto.

Restituirle come corpo produrrebbe una voce di forma perfetta — ``ArchivedEntry``
non ha modo di accorgersene — il cui "fatto" è ``id: a1b2c3d4``. Quella voce
entrerebbe in ``list_archived``, e da lì nell'elenco che il modello legge quando
cerca cosa sapeva: una risposta sbagliata detta con la stessa faccia di una
giusta. Meglio dire "illeggibile", che è vero e si nota.

Quel ramo non aveva test: misurato il 23/08, farlo ritornare le righe
d'intestazione come corpo sopravviveva alla suite intera.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jenny.agent.memory_archive import list_archived, read_archived


def _write(memory_dir: Path, name: str, text: str) -> Path:
    path = memory_dir / "archive" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestAnUnterminatedHeaderIsUnreadable:
    def test_a_truncated_file_is_none_and_not_a_fact_shaped_metadata(self, tmp_path):
        path = _write(
            tmp_path,
            "2026-03-01-aaaa1111.md",
            "---\nid: aaaa1111\nsource: USER.md\nheading: Preferences\n",
        )

        assert read_archived(path) is None

    @pytest.mark.parametrize(
        "text",
        [
            # Il caso minimo: solo il separatore di apertura.
            "---\n",
            # Troncato subito dopo la prima chiave.
            "---\nid: bbbb2222\n",
            # Una riga senza ``:`` in mezzo ai metadati non chiude niente: senza
            # il ``---`` finale resta un file troncato, non una voce con un corpo.
            "---\nid: bbbb2222\nnon una chiave\n",
        ],
    )
    def test_whatever_the_header_stopped_at(self, tmp_path, text):
        path = _write(tmp_path, "2026-03-02-bbbb2222.md", text)

        assert read_archived(path) is None

    def test_it_does_not_reach_the_list_the_model_reads(self, tmp_path):
        """La conseguenza visibile: l'elenco salta il file troncato e tiene l'altro.

        Non lo tiene con il corpo vuoto e non fa cadere l'intero elenco: un file
        rovinato costa un file.
        """
        _write(tmp_path, "2026-03-01-aaaa1111.md", "---\nid: aaaa1111\nsource: USER.md\n")
        _write(
            tmp_path,
            "2026-03-02-bbbb2222.md",
            "---\nid: bbbb2222\nsource: USER.md\n---\n\nIl ficus sta sul balcone\n",
        )

        entries = list_archived(tmp_path)

        assert [e.id for e in entries] == ["bbbb2222"]
        assert entries[0].text == "Il ficus sta sul balcone"

    def test_a_closed_header_with_nothing_under_it_is_unreadable_too(self, tmp_path):
        """Stesso esito per la stessa ragione: quel che resta non è un fatto."""
        path = _write(tmp_path, "2026-03-03-cccc3333.md", "---\nid: cccc3333\n---\n\n")

        assert read_archived(path) is None
