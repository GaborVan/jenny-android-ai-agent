"""Validate a Jenny App folder (app.json manifest + files).

Usage:
    python validate_app.py apps/<slug>

Prints errors (must fix) and warnings (should fix), then VALID/INVALID.
Designed to be run via python_exec with cwd = workspace root.
"""

import json
import re
import sys
from pathlib import Path

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
COLLECTION_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")
STORAGE_OPS = {"append", "set", "update", "delete", "query"}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
SECRET_KEYS = {"token", "password", "apikey", "api_key", "key", "secret", "bearer"}


def validate_action(action, index, has_server, errors, warnings):
    where = f"actions[{index}]"
    if not isinstance(action, dict):
        errors.append(f"{where}: must be an object")
        return None

    name = action.get("name")
    if not isinstance(name, str) or not ACTION_NAME_RE.match(name):
        errors.append(f"{where}: 'name' must be snake_case (got {name!r})")
    else:
        where = f"actions[{index}] ({name})"
    if not isinstance(action.get("description"), str) or not action["description"].strip():
        errors.append(f"{where}: 'description' is required")

    params = action.get("params", {})
    if not isinstance(params, dict):
        errors.append(f"{where}: 'params' must be an object mapping name -> JSON Schema")
        params = {}
    for pname, schema in params.items():
        if not isinstance(schema, dict) or "type" not in schema:
            errors.append(f"{where}: param '{pname}' must be a JSON Schema object with 'type'")
    required = action.get("required", [])
    if not isinstance(required, list) or any(r not in params for r in required):
        errors.append(f"{where}: 'required' must list a subset of params {sorted(params)}")

    kind = action.get("kind")
    if kind == "storage":
        if action.get("op") not in STORAGE_OPS:
            errors.append(f"{where}: 'op' must be one of {sorted(STORAGE_OPS)}")
        if action.get("op") == "query" and "limit" in params:
            warnings.append(
                f"{where}: param 'limit' is reserved on query actions (page size, "
                "not a filter) — rename the param if you meant to filter by it"
            )
        collection = action.get("collection")
        if not isinstance(collection, str) or not COLLECTION_RE.match(collection):
            errors.append(f"{where}: 'collection' must be lowercase alphanumeric/hyphens")
    elif kind == "http":
        if action.get("method") not in HTTP_METHODS:
            errors.append(f"{where}: 'method' must be one of {sorted(HTTP_METHODS)}")
        path = action.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            errors.append(f"{where}: 'path' must start with '/'")
        else:
            missing = [p for p in PLACEHOLDER_RE.findall(path) if p not in params]
            if missing:
                errors.append(f"{where}: path placeholders not declared in params: {missing}")
        if not has_server:
            errors.append(f"{where}: kind 'http' requires a top-level 'server.baseUrl'")
    else:
        errors.append(f"{where}: 'kind' must be 'storage' or 'http' (got {kind!r})")
    return name


def find_raw_secrets(node, path, errors):
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            if (
                key.lower() in SECRET_KEYS
                and key != "secretRef"
                and isinstance(value, str)
                and value.strip()
            ):
                errors.append(
                    f"{child}: looks like a raw secret — use \"auth\": {{\"secretRef\": \"<name>\"}} instead"
                )
            find_raw_secrets(value, child, errors)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            find_raw_secrets(item, f"{path}[{i}]", errors)


def validate_app(app_dir):
    app_dir = Path(app_dir)
    errors, warnings = [], []

    if not app_dir.is_dir():
        return [f"{app_dir}: not a directory"], warnings
    slug = app_dir.name
    if not SLUG_RE.match(slug) or len(slug) > 32:
        errors.append(f"folder name '{slug}' must be a valid slug (lowercase, hyphens, <=32 chars)")
    if "ui" in app_dir.parts:
        errors.append("app lives under ui/ — it will be overwritten on startup; move it to apps/")

    manifest_path = app_dir / "app.json"
    if not manifest_path.is_file():
        return errors + ["app.json is missing"], warnings
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return errors + [f"app.json: invalid JSON ({exc})"], warnings
    if not isinstance(manifest, dict):
        return errors + ["app.json: top level must be an object"], warnings

    for field in ("name", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"app.json: '{field}' is required")

    server = manifest.get("server")
    has_server = False
    if server is not None:
        if not isinstance(server, dict) or not isinstance(server.get("baseUrl"), str):
            errors.append("app.json: 'server' must be an object with a 'baseUrl' string")
        else:
            has_server = True
            if not server["baseUrl"].startswith(("http://", "https://")):
                errors.append("app.json: server.baseUrl must start with http:// or https://")

    find_raw_secrets(manifest, "app.json", errors)

    actions = manifest.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append("app.json: 'actions' must be a non-empty array")
    else:
        names = [validate_action(a, i, has_server, errors, warnings) for i, a in enumerate(actions)]
        dupes = {n for n in names if n and names.count(n) > 1}
        if dupes:
            errors.append(f"app.json: duplicate action names: {sorted(dupes)}")

    index_path = app_dir / "app" / "index.html"
    if not index_path.is_file():
        errors.append("app/index.html is missing (the UI lives in the app/ subfolder)")
    else:
        html = index_path.read_text(encoding="utf-8", errors="replace")
        externals = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html)
        if externals:
            errors.append(
                f"index.html references {len(externals)} external URL(s) — no external hosts "
                "allowed; only inline code and gateway paths (/html-mobile/assets/...)"
            )
        # Capacità della sandbox. L'iframe delle app è `sandbox="allow-scripts"` e
        # nient'altro (vedi mobile-apps.js), quindi i pattern qui sotto sono morti a
        # runtime *in silenzio*: l'app sembra finita e non fa niente quando la tocchi.
        # Erano documentati in references/manifest.md ma nessuno li applicava, così
        # un'app rotta passava la validazione senza un rilievo.
        if re.search(r"<form\b", html, re.IGNORECASE):
            errors.append(
                "index.html contains a <form> — the app iframe has no 'allow-forms', so "
                "submission is blocked before the 'submit' event is fired and "
                "event.preventDefault() never runs. Drop the <form>: use a "
                '<button type="button"> with a click handler, add a keydown listener for '
                "Enter on the input, and call jenny.action() from the handler"
            )
        modals = [fn for fn in ("alert", "confirm", "prompt") if re.search(rf"\b{fn}\s*\(", html)]
        if modals:
            warnings.append(
                "app/index.html looks like it calls "
                + ", ".join(f"{fn}()" for fn in modals)
                + " — the iframe has no 'allow-modals', so these silently do nothing; "
                "build dialogs with <dialog> or kit markup"
            )
        if "jenny-kit.css" not in html:
            warnings.append(
                "app/index.html does not link the Jenny Kit "
                "(/html-mobile/assets/apps/jenny-kit.css) — the UI will not match the app theme"
            )
        if "jenny-sdk.js" not in html:
            warnings.append(
                "app/index.html does not load the SDK (/html-mobile/assets/apps/jenny-sdk.js) "
                "— use jenny.action() for all action calls"
            )
        if re.search(r"fetch\s*\(\s*[`'\"][^)]*?/api/apps/", html):
            warnings.append(
                "app/index.html calls /api/apps/ with fetch directly — the gateway is GET-only "
                "and CORS-preflight-free; always go through jenny.action()"
            )
    if not (app_dir / "AGENT.md").is_file():
        warnings.append("AGENT.md is missing — add 5-15 lines of context for the agent")

    return errors, warnings


def main(argv):
    if len(argv) < 2:
        print("Usage: validate_app.py apps/<slug>")
        return
    errors, warnings = validate_app(argv[1])
    for msg in errors:
        print(f"ERROR: {msg}")
    for msg in warnings:
        print(f"WARNING: {msg}")
    print("INVALID" if errors else "VALID", f"({len(errors)} errors, {len(warnings)} warnings)")


if __name__ == "__main__":
    main(sys.argv)
