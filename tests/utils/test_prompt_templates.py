"""Test per jenny.utils.prompt_templates — rendering Jinja2 dei prompt di sistema.

I template sotto ``jenny/templates/`` cambiano il comportamento dell'agente
esattamente come codice: qui copriamo il rendering (variabili sostituite,
``strip``, ``{% include %}``, template mancante -> errore chiaro) e un guard
che ogni nome di template referenziato via ``render_template(...)`` nel
pacchetto ``jenny`` esista davvero su disco (un refuso nel nome sarebbe un
``TemplateNotFound`` silenzioso solo a runtime).
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

from jenny.utils import prompt_templates
from jenny.utils.prompt_templates import render_template

_JENNY_PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "jenny"
_BUNDLED_TEMPLATES_ROOT = _JENNY_PACKAGE_ROOT / "templates"


@pytest.fixture(autouse=True)
def _isolated_template_root(tmp_path, monkeypatch):
    """Punta l'ambiente Jinja (cache globale via ``lru_cache``) a ``tmp_path``.

    ``_environment()`` è cacheata a livello di processo: senza invalidarla,
    il primo test dell'intera suite che chiama ``render_template`` fissa per
    sempre la root del ``FileSystemLoader``. Puliamo prima e dopo ogni test
    per isolare completamente ciascun caso.
    """
    from jenny.runtime.context import get_runtime_context

    monkeypatch.setattr(get_runtime_context(), "workspace_dir", tmp_path)
    prompt_templates._environment.cache_clear()
    yield
    prompt_templates._environment.cache_clear()


def _write_template(tmp_path: Path, relative_name: str, content: str) -> Path:
    path = tmp_path / relative_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Rendering di base — sostituzione variabili
# ---------------------------------------------------------------------------


def test_renders_plain_text_without_variables(tmp_path):
    # jinja2 di default (``keep_trailing_newline=False``) strippa un singolo
    # newline finale dal template sorgente: comportamento standard di Jinja2,
    # non specifico di questo modulo, ma vale documentarlo qui.
    _write_template(tmp_path, "plain.md", "Hello, world.\n")
    assert render_template("plain.md") == "Hello, world."


def test_substitutes_single_variable(tmp_path):
    # NB: il kwarg si chiama "person", non "name" — "name" è già il primo
    # parametro posizionale di render_template() (il nome del template).
    _write_template(tmp_path, "greet.md", "Hello, {{ person }}!\n")
    assert render_template("greet.md", person="Jenny") == "Hello, Jenny!"


def test_substitutes_multiple_variables(tmp_path):
    _write_template(
        tmp_path,
        "multi.md",
        "max_iterations={{ max_iterations }} workspace={{ workspace }}\n",
    )
    result = render_template("multi.md", max_iterations=5, workspace="/tmp/ws")
    assert result == "max_iterations=5 workspace=/tmp/ws"


def test_missing_variable_renders_as_empty_string(tmp_path):
    """Jinja2 di default rende una variabile non passata come stringa vuota (no KeyError)."""
    _write_template(tmp_path, "undefined.md", "value=[{{ missing }}]\n")
    assert render_template("undefined.md") == "value=[]"


def test_does_not_html_escape_variable_values(tmp_path):
    """I prompt sono plain-text: ``autoescape`` deve restare disattivato."""
    _write_template(tmp_path, "raw.md", "{{ payload }}")
    result = render_template("raw.md", payload="<tool_call>&特</tool_call>")
    assert result == "<tool_call>&特</tool_call>"


# ---------------------------------------------------------------------------
# strip=True
# ---------------------------------------------------------------------------


def test_strip_false_preserves_trailing_newline(tmp_path):
    # Il sorgente ha due newline finali: jinja2 ne strippa uno solo (default
    # ``keep_trailing_newline=False``), lasciandone uno quando strip=False.
    _write_template(tmp_path, "trailing.md", "line one\n\n")
    assert render_template("trailing.md") == "line one\n"


def test_strip_true_removes_trailing_whitespace(tmp_path):
    _write_template(tmp_path, "trailing.md", "line one\n\n")
    assert render_template("trailing.md", strip=True) == "line one"


def test_strip_true_does_not_affect_leading_whitespace(tmp_path):
    _write_template(tmp_path, "leading.md", "\n\nline one")
    assert render_template("leading.md", strip=True) == "\n\nline one"


# ---------------------------------------------------------------------------
# trim_blocks / lstrip_blocks — comportamento dei blocchi Jinja nei prompt
# ---------------------------------------------------------------------------


def test_trim_and_lstrip_blocks_avoid_stray_whitespace_around_tags(tmp_path):
    _write_template(
        tmp_path,
        "block.md",
        "before\n{% if flag %}\nyes\n{% endif %}\nafter\n",
    )
    result = render_template("block.md", flag=True)
    assert result == "before\nyes\nafter"


def test_conditional_block_false_branch_omitted(tmp_path):
    _write_template(
        tmp_path,
        "block.md",
        "before\n{% if flag %}\nyes\n{% endif %}\nafter\n",
    )
    result = render_template("block.md", flag=False)
    assert result == "before\nafter"


# ---------------------------------------------------------------------------
# {% include %} — meccanismo usato per gli snippet condivisi (_snippets/)
# ---------------------------------------------------------------------------


def test_include_directive_pulls_in_shared_snippet(tmp_path):
    # ``trim_blocks``/``lstrip_blocks`` rimuovono il newline subito dopo il tag
    # di include e quello finale del file (default jinja2): il contenuto
    # incluso finisce quindi incollato a "bottom" senza newline in mezzo.
    _write_template(tmp_path, "agent/_snippets/shared.md", "SHARED CONTENT")
    _write_template(
        tmp_path,
        "agent/main.md",
        "top\n{% include 'agent/_snippets/shared.md' %}\nbottom\n",
    )
    result = render_template("agent/main.md")
    assert result == "top\nSHARED CONTENTbottom"


def test_include_of_missing_snippet_raises_template_not_found(tmp_path):
    _write_template(
        tmp_path,
        "agent/main.md",
        "{% include 'agent/_snippets/does_not_exist.md' %}",
    )
    with pytest.raises(jinja2.TemplateNotFound):
        render_template("agent/main.md")


# ---------------------------------------------------------------------------
# Template mancante -> errore chiaro
# ---------------------------------------------------------------------------


def test_missing_template_raises_template_not_found(tmp_path):
    with pytest.raises(jinja2.TemplateNotFound) as exc_info:
        render_template("agent/does_not_exist.md")
    # Il nome del template richiesto deve comparire nell'errore (diagnosticabilità).
    assert "does_not_exist" in str(exc_info.value)


def test_missing_template_error_names_the_template_not_a_generic_message(tmp_path):
    with pytest.raises(jinja2.TemplateNotFound) as exc_info:
        render_template("totally/unknown/path.md")
    assert "totally/unknown/path.md" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Guard: ogni nome di template referenziato via render_template() nel codice
# esiste realmente nel pacchetto bundled jenny/templates/ (non nella tmp_path
# di isolamento di questo test: qui vogliamo verificare i file reali del repo).
# ---------------------------------------------------------------------------


def _literal_template_names_referenced_in_source() -> set[str]:
    """Estrae, via regex, tutti i letterali passati come primo argomento a
    ``render_template(...)`` nel sorgente del pacchetto ``jenny``.

    Deliberatamente semplice (nessun parsing AST): cattura sia la forma
    single-line (``render_template("x.md", ...)``) sia quella multi-linea con
    il letterale sulla riga successiva alla chiamata.
    """
    pattern = re.compile(r"render_template\(\s*\n?\s*[\"']([^\"']+)[\"']")
    names: set[str] = set()
    for py_file in _JENNY_PACKAGE_ROOT.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        names.update(pattern.findall(text))
    return names


def test_every_referenced_template_name_exists_on_disk():
    """Guard anti-refuso: ogni nome usato in una chiamata reale a
    ``render_template`` deve corrispondere a un file bundled esistente."""
    referenced = _literal_template_names_referenced_in_source()
    assert referenced, "atteso almeno un nome di template referenziato nel sorgente"

    missing = [
        name for name in referenced if not (_BUNDLED_TEMPLATES_ROOT / name).is_file()
    ]
    assert not missing, f"template referenziati ma assenti su disco: {missing}"


def test_referenced_template_guard_actually_detects_a_missing_file(tmp_path):
    """Contro-prova che il guard sopra non sia tautologico: un nome non
    presente sotto templates/ deve fallire il medesimo controllo."""
    fake_name = "agent/this_file_does_not_exist_in_repo.md"
    assert not (_BUNDLED_TEMPLATES_ROOT / fake_name).is_file()
