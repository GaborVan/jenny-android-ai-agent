"""Test per gli script della skill app-creator (`validate_app.py`).

Il validatore gira dall'agente via `python_exec` ed è l'unico punto che può
fermare un'app generata prima che l'utente la apra. Finché non controllava le
capacità della sandbox certificava `VALID` app morte in silenzio: da qui i test.
"""

import importlib
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "jenny" / "skills" / "app-creator" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

validate_app_module = importlib.import_module("validate_app")

MANIFEST = """{
  "name": "Todo",
  "description": "A plain todo list",
  "actions": [
    {
      "name": "add_task",
      "description": "Append a task",
      "kind": "storage",
      "op": "append",
      "collection": "tasks",
      "params": {"text": {"type": "string"}},
      "required": ["text"]
    }
  ]
}"""

# Preamble condiviso: linka kit e SDK, così i warning che restano nei test sono
# solo quelli che il singolo caso vuole verificare.
HEAD = (
    '<link rel="stylesheet" href="/html-mobile/assets/apps/jenny-kit.css">'
    '<script src="/html-mobile/assets/apps/jenny-sdk.js"></script>'
)


def _write_app(tmp_path: Path, body: str, slug: str = "todo") -> Path:
    app_dir = tmp_path / "apps" / slug
    (app_dir / "app").mkdir(parents=True)
    (app_dir / "app.json").write_text(MANIFEST, encoding="utf-8")
    (app_dir / "AGENT.md").write_text("A todo list.\n", encoding="utf-8")
    (app_dir / "app" / "index.html").write_text(f"<html><head>{HEAD}</head>{body}</html>", "utf-8")
    return app_dir


def test_form_is_rejected_as_an_error(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        '<body><form id="f"><input id="t"><button type="submit">Add</button></form></body>',
    )

    errors, _ = validate_app_module.validate_app(app_dir)

    assert any("allow-forms" in e for e in errors), errors


def test_form_is_rejected_even_with_a_submit_handler(tmp_path: Path) -> None:
    """Il rimedio documentato in passato non funziona: bloccare comunque.

    L'invio è bloccato prima che l'evento `submit` sia emesso, quindi un handler
    con `preventDefault()` non viene mai chiamato e l'app resta morta.
    """
    app_dir = _write_app(
        tmp_path,
        "<body><form id=\"f\"><input id=\"t\"></form>"
        '<script>document.getElementById("f").addEventListener("submit", function (e) {'
        ' e.preventDefault(); jenny.action("add_task", {text: "x"}); });</script></body>',
    )

    errors, _ = validate_app_module.validate_app(app_dir)

    assert any("allow-forms" in e for e in errors), errors


def test_form_detection_is_case_insensitive(tmp_path: Path) -> None:
    app_dir = _write_app(tmp_path, '<body><FORM><INPUT id="t"></FORM></body>')

    errors, _ = validate_app_module.validate_app(app_dir)

    assert any("allow-forms" in e for e in errors), errors


def test_the_word_form_in_prose_is_not_a_false_positive(tmp_path: Path) -> None:
    app_dir = _write_app(tmp_path, "<body><p>Fill in the form below to add a task.</p></body>")

    errors, _ = validate_app_module.validate_app(app_dir)

    assert not any("allow-forms" in e for e in errors), errors


def test_button_with_click_handler_is_accepted(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        '<body><div><input id="t"><button id="b" type="button">Add</button></div>'
        '<script>document.getElementById("b").addEventListener("click", function () {'
        ' jenny.action("add_task", {text: document.getElementById("t").value}); });'
        'document.getElementById("t").addEventListener("keydown", function (e) {'
        ' if (e.key === "Enter") document.getElementById("b").click(); });</script></body>',
    )

    errors, warnings = validate_app_module.validate_app(app_dir)

    assert errors == []
    assert warnings == []


def test_modal_calls_are_warnings_not_errors(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        '<body><button id="b" type="button">Del</button>'
        '<script>if (confirm("sure?")) { alert("done"); }</script></body>',
    )

    errors, warnings = validate_app_module.validate_app(app_dir)

    assert errors == []
    assert any("allow-modals" in w for w in warnings), warnings
    joined = " ".join(warnings)
    assert "confirm()" in joined and "alert()" in joined


def test_valid_app_reports_no_errors_end_to_end(tmp_path: Path) -> None:
    """Guardia contro un validatore che diventa a prova di tutto: un'app corretta
    deve restare corretta, altrimenti le nuove regole bloccano il lavoro buono."""
    app_dir = _write_app(tmp_path, '<body><button type="button">Add</button></body>')

    errors, warnings = validate_app_module.validate_app(app_dir)

    assert (errors, warnings) == ([], [])
