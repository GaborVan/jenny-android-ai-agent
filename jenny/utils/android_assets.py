from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    pass

_TEMPLATES_MANIFEST = [
    "AGENTS.md", "SOUL.md", "USER.md", "HEARTBEAT.md",
    "agent/identity.md", "agent/tool_contract.md", "agent/subagent_system.md",
    "agent/subagent_announce.md", "agent/skills_section.md", "agent/apps_section.md",
    "agent/platform_policy.md", "agent/cron_reminder.md",
    "agent/max_iterations_message.md", "agent/consolidator_archive.md",
    "agent/dream.md", "agent/evaluator.md", "agent/orchestrator.md",
    "agent/tool_inventory.md",
    "agent/_snippets/untrusted_content.md",
    "agent/types/researcher.md", "agent/types/writer.md", "agent/types/coder.md",
    "agent/types/analyst.md", "agent/types/sysadmin.md", "agent/types/operator.md",
    "memory/MEMORY.md",
]

_SKILLS_MANIFEST = [
    "cron/SKILL.md",
    "long-goal/SKILL.md",
    # llm-wiki
    "llm-wiki/SKILL.md",
    "llm-wiki/scripts/audit_review.py",
    "llm-wiki/scripts/lint_wiki.py",
    "llm-wiki/scripts/reindex_wikis.py",
    "llm-wiki/scripts/scaffold.py",
    "llm-wiki/references/article-guide.md",
    "llm-wiki/references/audit-guide.md",
    "llm-wiki/references/log-guide.md",
    "llm-wiki/references/schema-guide.md",
    "llm-wiki/references/tooling-tips.md",
    # my
    "my/SKILL.md",
    "my/references/examples.md",
    # http-client
    "http-client/SKILL.md",
    # skill-creator
    "skill-creator/SKILL.md",
    "skill-creator/scripts/init_skill.py",
    "skill-creator/scripts/quick_validate.py",
    "skill-creator/scripts/package_skill.py",
    # app-creator
    "app-creator/SKILL.md",
    "app-creator/references/manifest.md",
    "app-creator/scripts/validate_app.py",
    # memory
    "memory/SKILL.md",
    # data-processing
    "data-processing/SKILL.md",
    # ssh
    "ssh/SKILL.md",
]

_UI_MANIFEST = [
    "assets/apps/jenny-charts.js",
    "assets/apps/jenny-kit.css",
    "assets/apps/jenny-sdk.js",
    "assets/bootstrap.js",
    "assets/i18n/en.json",
    "assets/i18n/it.json",
    "assets/jenny-fall-color.webp",
    "assets/jenny-fall.webp",
    "assets/jenny-ground-color.webp",
    "assets/jenny-ground.webp",
    "assets/jenny-hang-color.webp",
    "assets/jenny-hang.webp",
    "assets/jenny-hello1-color.webp",
    "assets/jenny-hello1.webp",
    "assets/jenny-hello2-color.webp",
    "assets/jenny-hello2.webp",
    "assets/jenny-idle-color.webp",
    "assets/jenny-idle.webp",
    "assets/jenny-side-talk-color.webp",
    "assets/jenny-side-talk.webp",
    "assets/jenny-side-color.webp",
    "assets/jenny-side.webp",
    "assets/jenny-talk1a-color.webp",
    "assets/jenny-talk1a.webp",
    "assets/jenny-talk1b-color.webp",
    "assets/jenny-talk1b.webp",
    "assets/jenny-talk2a-color.webp",
    "assets/jenny-talk2a.webp",
    "assets/jenny-talk2b-color.webp",
    "assets/jenny-talk2b.webp",
    "assets/jenny-think-color.webp",
    "assets/jenny-think.webp",
    "assets/jenny-walk1-color.webp",
    "assets/jenny-walk1.webp",
    "assets/jenny-walk2-color.webp",
    "assets/jenny-walk2.webp",
    "assets/jenny.png",
    "assets/mobile-app.js",
    "assets/mobile-apps.js",
    "assets/mobile-chat.js",
    "assets/mobile-drawer.js",
    "assets/mobile-graph.js",
    "assets/mobile-header.js",
    "assets/mobile-jenny.js",
    "assets/mobile-onboarding.js",
    "assets/mobile-settings.js",
    "assets/mobile-style.css",
    "assets/mobile-ui-query.js",
    "assets/mobile-wiki.js",
    "assets/mobile-workspace.js",
    "assets/shared/advanced-mode.js",
    "assets/shared/api-client.js",
    "assets/shared/backup-flow.js",
    "assets/shared/dialog.js",
    "assets/shared/home-view.js",
    "assets/shared/i18n.js",
    "assets/shared/image-handler.js",
    "assets/shared/image-lightbox.js",
    "assets/shared/keyboard.js",
    "assets/shared/longpress.js",
    "assets/shared/mascot.js",
    "assets/shared/pinch-zoom.js",
    "assets/shared/provider-brand.js",
    "assets/shared/session-manager.js",
    "assets/shared/state.js",
    "assets/shared/subagent-policy.js",
    "assets/shared/telegram-pairing.js",
    "assets/shared/theme.js",
    "assets/shared/tree-renderer.js",
    "assets/shared/utils.js",
    "assets/shared/ws-manager.js",
    "assets/vendor/@tabler/icons-webfont@3.19.0/LICENSE",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.ttf",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.woff",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/fonts/tabler-icons.woff2",
    "assets/vendor/@tabler/icons-webfont@3.19.0/dist/tabler-icons.min.css",
    "assets/vendor/codemirror@5.65.16/LICENSE",
    "assets/vendor/codemirror@5.65.16/addon/mode/simple.min.js",
    "assets/vendor/codemirror@5.65.16/lib/codemirror.min.css",
    "assets/vendor/codemirror@5.65.16/lib/codemirror.min.js",
    "assets/vendor/codemirror@5.65.16/mode/clike/clike.min.js",
    "assets/vendor/codemirror@5.65.16/mode/css/css.min.js",
    "assets/vendor/codemirror@5.65.16/mode/go/go.min.js",
    "assets/vendor/codemirror@5.65.16/mode/javascript/javascript.min.js",
    "assets/vendor/codemirror@5.65.16/mode/markdown/markdown.min.js",
    "assets/vendor/codemirror@5.65.16/mode/python/python.min.js",
    "assets/vendor/codemirror@5.65.16/mode/rust/rust.min.js",
    "assets/vendor/codemirror@5.65.16/mode/shell/shell.min.js",
    "assets/vendor/codemirror@5.65.16/mode/xml/xml.min.js",
    "assets/vendor/codemirror@5.65.16/mode/yaml/yaml.min.js",
    "assets/vendor/codemirror@5.65.16/theme/darcula.min.css",
    "assets/vendor/codemirror@5.65.16/theme/eclipse.min.css",
    "assets/vendor/d3@7/LICENSE",
    "assets/vendor/d3@7/d3.min.js",
    "assets/vendor/dompurify@3/purify.min.js",
    "assets/vendor/fonts/LICENSE.txt",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa0ZL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1ZL7.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa1pL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa25L7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2JL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2ZL7SUc.woff2",
    "assets/vendor/fonts/UcC73FwrK3iLTeHuS_nVMrMxCp50SjIa2pL7SUc.woff2",
    "assets/vendor/fonts/google-fonts.css",
    "assets/vendor/fonts/baloo-2-700.woff2",
    "assets/vendor/fonts/bangers-400.woff2",
    "assets/vendor/fonts/comic-neue-400.woff2",
    "assets/vendor/fonts/comic-neue-700.woff2",
    "assets/vendor/fonts/fredoka-600.woff2",
    "assets/vendor/fonts/marcellus-400.woff2",
    "assets/vendor/fonts/orbitron-500.woff2",
    "assets/vendor/fonts/orbitron-700.woff2",
    "assets/vendor/fonts/shippori-mincho-500.woff2",
    "assets/vendor/fonts/tenor-sans-400.woff2",
    "assets/vendor/fonts/theme-fonts.css",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh09SDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh0NSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh0dSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh2dSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh3dSD.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bh3tSDulI.woff2",
    "assets/vendor/fonts/uU9NCBsR6Z2vfE9aq3bhZ_Wmh2uX.woff2",
    "assets/vendor/highlight.js@11.11.1/LICENSE",
    "assets/vendor/highlight.js@11.11.1/build/highlight.min.js",
    "assets/vendor/highlight.js@11.11.1/styles/github-dark.min.css",
    "assets/vendor/highlight.js@11.11.1/styles/github.min.css",
    "assets/vendor/katex@0.16.10/LICENSE",
    "assets/vendor/katex@0.16.10/dist/contrib/auto-render.min.js",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_AMS-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Caligraphic-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Fraktur-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-BoldItalic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Main-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-BoldItalic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Math-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Bold.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Italic.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_SansSerif-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Script-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size1-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size2-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size3-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Size4-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.ttf",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.woff",
    "assets/vendor/katex@0.16.10/dist/fonts/KaTeX_Typewriter-Regular.woff2",
    "assets/vendor/katex@0.16.10/dist/katex.min.css",
    "assets/vendor/katex@0.16.10/dist/katex.min.js",
    "assets/vendor/marked@15.0.7/LICENSE",
    "assets/vendor/marked@15.0.7/marked.min.js",
    "assets/vendor/mermaid@10/LICENSE",
    "assets/vendor/mermaid@10/dist/mermaid.min.js",
    "index.html",
]

_extracted_registry: dict[str, Path] = {}


def read_asset(module: str, path: str) -> bytes | None:
    try:
        from importlib.resources import files as _files

        return (_files(module) / path).read_bytes()
    except (TypeError, AttributeError, FileNotFoundError, ModuleNotFoundError):
        pass
    try:
        import pkgutil

        data = pkgutil.get_data(module, path)
        if data is not None:
            return data
    except Exception:
        pass
    if path.endswith(".py"):
        # Best-effort fallback — try to read .py sources from the APK.
        # May fail on Android 11+ where /data/app/ and /proc/self/maps
        # are restricted. Skill scripts are copied as raw assets via
        # Gradle; this fallback catches edge cases.
        try:
            import glob
            import zipfile

            apks = glob.glob("/data/app/*/base.apk")
            if not apks:
                with open("/proc/self/maps") as _f:
                    for _line in _f:
                        if "base.apk" in _line:
                            _parts = _line.split()
                            if len(_parts) > 5:
                                apks = [_parts[-1]]
                                break
            for _apk in apks:
                try:
                    with zipfile.ZipFile(_apk, "r") as _zf:
                        _asset_path = f"assets/skills/{path}"
                        try:
                            _info = _zf.getinfo(_asset_path)
                            return _zf.read(_info)
                        except KeyError:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
        logger.debug("Could not read source for {} {}", module, path)
    return None


def _get_manifest(package: str) -> list[str] | None:
    if package == "jenny.templates":
        return _TEMPLATES_MANIFEST
    if package == "jenny.templates.ui":
        return _UI_MANIFEST
    if package == "jenny.skills":
        return _SKILLS_MANIFEST
    return None


def _write_bytes_force(target: Path, data: bytes) -> None:
    """Scrive *data* su *target*, sopravvivendo a un file mirror read-only.

    Il mirror ``workspace/ui`` non è più autoritativo (le UI attive sono servite
    dai byte impacchettati), ma la sync lo riscrive ad ogni boot. Se un file era
    stato reso read-only, ``write_bytes`` fallirebbe con ``PermissionError`` e
    manderebbe in crash-loop il gateway al boot: qui riportiamo il file
    scrivibile (o lo rimuoviamo, la dir è nostra) e riscriviamo.
    """
    try:
        target.write_bytes(data)
        return
    except PermissionError:
        pass
    try:
        target.chmod(0o644)
    except OSError:
        target.unlink(missing_ok=True)
    target.write_bytes(data)


def extract_package_dir(package: str, dest: Path, *, skip_existing: bool = False) -> int:
    manifest = _get_manifest(package)
    if manifest is None:
        # Design a manifest esplicito (vedi .agent/gotchas.md): un package senza
        # manifest non deve mai finire in un walk silenzioso, che sul dispositivo
        # farebbe arrivare/mancare file senza traccia. Fallire esplicito.
        raise ValueError(
            f"no static manifest registered for package {package!r} "
            "(add it to _TEMPLATES_MANIFEST/_UI_MANIFEST/_SKILLS_MANIFEST)"
        )
    files = manifest
    logger.debug("Using static manifest for {}", package)

    if not files:
        logger.warning("Manifest for package {} is empty", package)
        return 0

    count = 0
    for rel_path in files:
        if rel_path.endswith(".DS_Store") or "__pycache__" in rel_path or rel_path.endswith(".pyc"):
            continue
        target = dest / rel_path
        if skip_existing and target.exists():
            continue
        data = read_asset(package, rel_path)
        if data is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_bytes_force(target, data)
            count += 1

    logger.info("Extracted {} files from {} to {}", count, package, dest)
    return count


_JENNY_SRC_KEY = "jenny_src"


def _find_apk_paths() -> list[str]:
    """Locate the installed APK (Android only). Mirrors read_asset's lookup."""
    import glob

    apks = glob.glob("/data/app/*/base.apk")
    if apks:
        return apks
    try:
        with open("/proc/self/maps") as f:
            for line in f:
                if "base.apk" in line:
                    parts = line.split()
                    if len(parts) > 5:
                        return [parts[-1]]
    except OSError:
        pass
    return []


def extract_jenny_source(dest: Path) -> int:
    """Extract jenny's plain .py sources bundled as APK assets.

    Chaquopy compiles ``jenny/**`` into an .imy archive, so the readable
    sources are mirrored as raw assets under ``assets/jenny_src/`` by the
    Gradle ``copyPackageSourceAssets`` task. This extracts them to a real
    on-device directory so filesystem tools and get_source can read them.
    """
    import zipfile

    asset_prefix = "assets/jenny_src/"
    count = 0
    for apk in _find_apk_paths():
        try:
            with zipfile.ZipFile(apk, "r") as zf:
                for info in zf.infolist():
                    if not info.filename.startswith(asset_prefix) or info.is_dir():
                        continue
                    rel = info.filename[len(asset_prefix):]
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(info))
                    count += 1
        except Exception:
            continue
        if count:
            break
    if count:
        _extracted_registry[_JENNY_SRC_KEY] = dest
        logger.info("Extracted {} jenny source files to {}", count, dest)
    else:
        logger.debug("No jenny source assets found to extract")
    return count


def get_package_source_root() -> Path | None:
    """Return the directory holding jenny's readable .py sources, if any.

    On Android this is the directory populated by ``extract_jenny_source``;
    on desktop/dev it is the installed package directory itself. Returns None
    when no plain-source tree exists (e.g. packaged build before extraction).
    """
    extracted = _extracted_registry.get(_JENNY_SRC_KEY)
    if extracted is not None and extracted.exists():
        return extracted
    try:
        import jenny

        pkg_dir = Path(jenny.__file__).resolve().parent
    except Exception:
        return None
    if (pkg_dir / "__init__.py").is_file():
        return pkg_dir
    return None
