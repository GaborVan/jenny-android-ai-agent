from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


def test_source_checkout_import_uses_pyproject_version_without_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    script = textwrap.dedent(
        f"""
        import sys

        sys.path.insert(0, {str(repo_root)!r})

        import jenny

        print(jenny.__version__)
        """
    )

    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


def test_hardcoded_fallback_matches_pyproject() -> None:
    """Il letterale di fallback è la versione che l'app mostra su Android.

    Là non esistono né i metadata del pacchetto né pyproject.toml, quindi se
    resta indietro l'app annuncia una versione vecchia senza che niente falli.
    """
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    source = (repo_root / "jenny" / "__init__.py").read_text(encoding="utf-8")

    assert f'_read_pyproject_version() or "{expected}"' in source, (
        f"il fallback in jenny/__init__.py non è {expected}: allinealo al bump di versione"
    )


def test_android_version_name_matches_pyproject() -> None:
    """``versionName`` è la versione che Android mostra in Impostazioni → App.

    Il commento nel build.gradle.kts chiedeva già di tenerli in pari, ma niente
    lo verificava: il branch è arrivato a 0.4.0-dev mentre pyproject era ancora
    a 0.3.1, e nessun test è fallito. Il pre-release suffix è ammesso durante lo
    sviluppo, la parte numerica no.
    """
    repo_root = Path(__file__).resolve().parents[1]
    expected = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    gradle = (repo_root / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")

    match = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    assert match is not None, "versionName non trovato in android/app/build.gradle.kts"

    version_name = match.group(1)
    base = version_name.split("-", 1)[0]
    assert base == expected, (
        f"versionName {version_name!r} non corrisponde a pyproject {expected!r}: "
        "allinea i due al bump di versione"
    )
