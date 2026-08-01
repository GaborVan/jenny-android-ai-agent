"""Agent-context summary of the installed Jenny Apps (progressive disclosure:
one line per app; AGENT.md stays lazy-read via the file tools)."""

from __future__ import annotations

from pathlib import Path

from jenny.apps.manifest import scan_apps


def build_apps_summary(workspace: Path) -> str:
    """One markdown bullet per app; empty string when there are no apps."""
    apps = scan_apps(workspace)
    if not apps:
        return ""
    lines: list[str] = []
    for app in apps:
        if app.broken or app.manifest is None:
            lines.append(
                f"- **{app.slug}** — BROKEN ({app.error}). "
                f"Fix `apps/{app.slug}/app.json` if the user asks."
            )
            continue
        tools = ", ".join(f"`{app.slug}_{a.name}`" for a in app.manifest.actions)
        line = (
            f"- **{app.manifest.name}** (`{app.slug}`) — {app.manifest.description} "
            f"— tools: {tools} — data: `apps/{app.slug}/data/`"
        )
        if (app.dir / "AGENT.md").is_file():
            line += f" — context: `apps/{app.slug}/AGENT.md`"
        lines.append(line)
    return "\n".join(lines)
