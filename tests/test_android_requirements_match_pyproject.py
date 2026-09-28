"""``pyproject.toml`` e ``requirements-android.txt`` devono dire la stessa cosa.

L'APK non installa `pyproject.toml`: Chaquopy legge `requirements-android.txt`
(vedi `android/app/build.gradle.kts`) e, tramite il lock accanto, ciò che finisce
davvero nel telefono. Il file dei requisiti dichiara a parole che i suoi range
sono «tenuti in sincrono» con `pyproject.toml` — una promessa che nessun test
verificava, e che si rompe in silenzio in due modi diversi:

* una dipendenza aggiunta a `pyproject.toml` e non qui gira nei test desktop e
  **non esiste** sul dispositivo: si scopre solo a runtime, sul telefono;
* un range allargato solo qui fa testare a CI vincoli che nessuno spedisce —
  cioè il verde della suite smette di dire qualcosa sull'APK.

Il confronto è sui range interi, non sulla sola presenza del pacchetto: è il
range il posto in cui la deriva vive (``pypdf>=5,<6`` contro ``pypdf>=6``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
ANDROID_REQUIREMENTS = REPO / "requirements-android.txt"


def _pyproject_dependencies() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text("utf-8"))
    return set(data["project"]["dependencies"])


def _android_requirements() -> set[str]:
    lines = ANDROID_REQUIREMENTS.read_text("utf-8").splitlines()
    # Le righe commentate sono la sezione «optional document support», che per
    # scelta non entra nell'APK: non sono requisiti e non vanno confrontate.
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def test_every_declared_dependency_ships_on_android() -> None:
    missing = sorted(_pyproject_dependencies() - _android_requirements())

    assert not missing, (
        "dipendenze di pyproject.toml assenti da requirements-android.txt: "
        f"{missing}. Il test runner le installa, l'APK no: il codice che le usa "
        "gira in CI e fallisce sul telefono. Aggiungile (o toglile dal progetto)."
    )


def test_android_requirements_are_exactly_the_declared_dependencies() -> None:
    extra = sorted(_android_requirements() - _pyproject_dependencies())

    assert not extra, (
        "vincoli in requirements-android.txt che pyproject.toml non dichiara: "
        f"{extra}. Sono il vincolo con cui l'APK viene davvero costruito: se non "
        "li dichiara il progetto, CI non li sta testando."
    )
