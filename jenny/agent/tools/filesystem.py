"""File system tools: read, write, edit, list."""

import difflib
import mimetypes
import os
from pathlib import Path
from typing import Any

from jenny.agent.tools.base import Tool, tool_parameters
from jenny.agent.tools.file_state import FileStates, _hash_file, current_file_states
from jenny.agent.tools.filesystem_edit_match import (
    _best_window,
    _find_matches,
    _preserve_quote_style,
    _reindent_like_match,
)
from jenny.agent.tools.path_utils import resolve_workspace_path
from jenny.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from jenny.config.tool_schemas import FileToolsConfig  # re-export (def in config.tool_schemas)
from jenny.security.workspace_access import current_tool_workspace
from jenny.security.workspace_policy import _safe_expanduser
from jenny.utils.helpers import build_image_content_blocks, detect_image_mime


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    config_key = "file"

    @classmethod
    def config_cls(cls):
        return FileToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.file.enable

    @classmethod
    def disabled_reason(cls, ctx: Any) -> str | None:
        """Un solo interruttore spegne tutti i tool sui file, e si vede poco.

        Senza questa frase un subagent che vive di filesystem — ``writer``,
        ``coder``, ``analyst`` — partirebbe con zero tool e riporterebbe di aver
        fallito, che si legge come un problema del modello invece che di
        un'impostazione.
        """
        if not ctx.config.file.enable:
            # Non "Settings > ...": quel pannello non esiste. Questo
            # interruttore vive solo in config.json, e mandare l'utente a
            # cercare una schermata inventata e peggio che non dirgli niente.
            return "file tools are off (tools.file.enable in config.json)"
        return None

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
        extra_read_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_files: list[Path] | None = None,
        file_states: FileStates | None = None,
        restrict_to_workspace: bool | None = None,
        write_files_only: bool = False,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        # "Nessuna directory scrivibile, solo questi file esatti". Serve a un
        # runner isolato che produce un unico artefatto (Atlas → memory/WIKI.md):
        # senza questo, ``allowed_dir=None`` significa "eredita la radice dello
        # scope", cioè l'intero workspace scrivibile. Va usato insieme a
        # ``extra_write_allowed_files``; da solo nega qualunque scrittura.
        self._write_files_only = write_files_only
        # Legacy alias: extra_allowed_dirs is read-only. Write-capable tools
        # must opt in via extra_write_allowed_dirs.
        self._extra_read_allowed_dirs = [
            *(extra_allowed_dirs or []),
            *(extra_read_allowed_dirs or []),
        ]
        self._extra_write_allowed_dirs = list(extra_write_allowed_dirs or [])
        self._extra_write_allowed_files = list(extra_write_allowed_files or [])
        self._restrict_to_workspace = (
            bool(restrict_to_workspace)
            if restrict_to_workspace is not None
            else allowed_dir is not None
        )
        # Explicit state is used by isolated runners like Dream/subagents.
        # Main AgentLoop tools leave this unset and resolve state from the
        # current async task, which keeps shared tool instances session-safe.
        self._explicit_file_states = file_states
        self._fallback_file_states = FileStates()

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        restrict = ctx.config.restrict_to_workspace
        allowed_dir = Path(ctx.workspace) if restrict else None
        extra_read = [Path(ctx.workspace) / "skills"]
        if getattr(ctx.config.file, "expose_package_source", False):
            from jenny.utils.android_assets import get_package_source_root

            source_root = get_package_source_root()
            if source_root is not None:
                extra_read.append(source_root)
        return cls(
            workspace=Path(ctx.workspace),
            allowed_dir=allowed_dir,
            extra_read_allowed_dirs=extra_read,
            file_states=ctx.file_state_store,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
        )

    @property
    def _file_states(self) -> FileStates:
        if self._explicit_file_states is not None:
            return self._explicit_file_states
        return current_file_states(self._fallback_file_states)

    def _effective_allowed_root(self, access_allowed_root: Path | None) -> Path | None:
        if self._allowed_dir is None or self._workspace is None:
            return access_allowed_root
        try:
            allowed_dir = _safe_expanduser(self._allowed_dir).resolve(strict=False)
            workspace = _safe_expanduser(self._workspace).resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return access_allowed_root if access_allowed_root is not None else self._allowed_dir
        if allowed_dir == workspace:
            return access_allowed_root
        return allowed_dir

    def _resolve_with_extra(
        self,
        path: str,
        extra_allowed_dirs: list[Path] | None,
        extra_allowed_files: list[Path] | None,
        *,
        include_media_dir: bool,
    ) -> Path:
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
        )
        return resolve_workspace_path(
            path,
            access.project_path,
            self._effective_allowed_root(access.allowed_root),
            extra_allowed_dirs,
            extra_allowed_files,
            include_media_dir=include_media_dir,
        )

    def _resolve_read(self, path: str) -> Path:
        return self._resolve_with_extra(
            path,
            self._extra_read_allowed_dirs,
            None,
            include_media_dir=True,
        )

    def _resolve_write(self, path: str) -> Path:
        # Punto di raccolta unico per l'intento di scrittura di tutti i tool
        # write-capable (write_file / edit_file / apply_patch): contarlo qui,
        # prima della risoluzione (che può sollevare ``PermissionError`` o
        # ``WorkspaceBoundaryError``), cattura anche le scritture bloccate.
        # Dream lo usa per non avanzare il cursore quando ha provato a scrivere
        # ma è stato bloccato.
        self._file_states.record_write_attempt()
        if self._write_files_only:
            # Bypassa ``_effective_allowed_root``: passare ``allowed_dir=None``
            # più un'allowlist di file non vuota fa scattare la modalità
            # "solo questi file" di ``resolve_allowed_path`` (fail-closed se
            # l'allowlist è vuota).
            access = current_tool_workspace(
                self._workspace,
                restrict_to_workspace=self._restrict_to_workspace,
            )
            return resolve_workspace_path(
                path,
                access.project_path,
                None,
                None,
                self._extra_write_allowed_files,
                include_media_dir=False,
            )
        return self._resolve_with_extra(
            path,
            self._extra_write_allowed_dirs,
            self._extra_write_allowed_files,
            include_media_dir=False,
        )

    def _resolve(self, path: str) -> Path:
        return self._resolve_read(path)

    def _display_workspace(self) -> Path | None:
        return current_tool_workspace(self._workspace).project_path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re
    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    # Check if resolved path starts with /dev/ (covers symlinks to devices)
    # Note: /dev/ layout differs on Android but blocking the entire subtree is
    # correct for security regardless of platform.
    if resolved.startswith("/dev/"):
        return True
    return False


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """Parse a page range like '2-5' into 0-based (start, end) inclusive."""
    parts = pages.strip().split("-")
    if len(parts) == 1:
        p = int(parts[0])
        return max(0, p - 1), min(p - 1, total - 1)
    start = int(parts[0])
    end = int(parts[1])
    return max(0, start - 1), min(end - 1, total - 1)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        force=BooleanSchema(
            description="Bypass same-file read deduplication and return content again.",
            default=False,
        ),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""
    _scopes = {"core", "orchestrator", "subagent"}

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or PDF document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Supports PDF documents. "
            "Use find_files/list_dir first when the path is uncertain. "
            "Read the relevant range before editing so replacements or patches "
            "are based on current content. "
            "Use offset and limit for large text files. "
            "Use force=true to re-read content even if unchanged. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
        pages: str | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"

            # Device path blacklist
            if _is_blocked_device(path):
                return f"Error: Reading {path} is blocked (device path that could hang or produce infinite output)."

            fp = self._resolve_read(path)
            if _is_blocked_device(fp):
                return f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output)."
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            # PDF support
            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            raw = fp.read_bytes()
            if not raw:
                return f"(Empty file: {path})"

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            # Read dedup: same path + offset + limit + unchanged mtime → stub
            # Always check for external modifications before dedup
            entry = self._file_states.get(fp)
            try:
                current_mtime = os.path.getmtime(fp)
            except OSError:
                current_mtime = 0.0
            if (
                not force
                and entry
                and entry.can_dedup
                and entry.offset == offset
                and entry.limit == limit
            ):
                if current_mtime != entry.mtime:
                    # File was modified externally - force full read and mark as not dedupable
                    entry.can_dedup = False
                    # Continue to read full content (don't return dedup message)
                else:
                    # File unchanged - return dedup message
                    # But only if content is actually unchanged (not just mtime)
                    current_hash = _hash_file(str(fp))
                    if current_hash == entry.content_hash:
                        return f"[File unchanged since last read: {path}]"
                    else:
                        # Content changed despite same mtime - force full read
                        entry.can_dedup = False
            else:
                # No previous state or marked as not dedupable - read full content
                # Force full read by setting can_dedup to False for this read
                if entry:
                    entry.can_dedup = False

            # Read the file content after dedup check
            raw = fp.read_bytes()
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file - return error message
                mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                if mime and mime.startswith("image/"):
                    return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")
                return f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only UTF-8 text and images are supported."

            # Normalize CRLF -> LF before line-splitting. Primarily a Windows
            # concern (git checkouts with autocrlf, editors saving CRLF) but
            # applied on all platforms so downstream StrReplace/Grep behavior
            # is consistent regardless of where the file was written.
            text_content = text_content.replace("\r\n", "\n")

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            self._file_states.record_read(fp, offset=offset, limit=limit)
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        from pypdf import PdfReader

        reader = PdfReader(str(fp))
        total_pages = len(reader.pages)
        if pages:
            try:
                start, end = _parse_page_range(pages, total_pages)
            except (ValueError, IndexError):
                return f"Error: Invalid page range '{pages}'. Use format like '1-5'."
            if start > end or start >= total_pages:
                return f"Error: Page range '{pages}' is out of bounds (document has {total_pages} pages)."
        else:
            start = 0
            end = min(total_pages - 1, self._MAX_PDF_PAGES - 1)

        if end - start + 1 > self._MAX_PDF_PAGES:
            end = start + self._MAX_PDF_PAGES - 1

        parts: list[str] = []
        for i in range(start, end + 1):
            text = (reader.pages[i].extract_text() or "").strip()
            if text:
                parts.append(f"--- Page {i + 1} ---\n{text}")

        if not parts:
            return f"(PDF has no extractable text: {fp})"

        result = "\n\n".join(parts)
        if end < total_pages - 1:
            result += (
                f"\n\n(Showing pages {start + 1}-{end + 1} of {total_pages}. "
                f"Use pages='{end + 2}-{min(end + 1 + self._MAX_PDF_PAGES, total_pages)}' "
                "to continue.)"
            )
        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(PDF text truncated at ~128K chars)"
        return result

# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """Write content to a file."""
    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a new file or intentionally replace an entire file with "
            "the provided content. Overwrites existing files and creates parent "
            "directories as needed. For code changes or partial edits, prefer "
            "apply_patch; use edit_file only for small exact replacements."
        )

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = self._resolve_write(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            self._file_states.record_write(fp)
            return f"Successfully wrote {len(content)} characters to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------





@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        occurrence=IntegerSchema(
            1,
            description="Optional 1-based occurrence to replace when old_text appears multiple times.",
            minimum=1,
            nullable=True,
        ),
        line_hint=IntegerSchema(
            1,
            description="Optional 1-based line hint used to choose the nearest match.",
            minimum=1,
            nullable=True,
        ),
        expected_replacements=IntegerSchema(
            1,
            description="Optional guard for the number of replacements that must be made.",
            minimum=1,
            nullable=True,
        ),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""
    _scopes = {"core", "subagent"}

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Perform a small, exact replacement in one file by replacing "
            "old_text with new_text. Use this for narrow text substitutions "
            "with old_text copied from read_file. For multi-file, structural, "
            "or generated code edits, prefer apply_patch. If old_text matches "
            "multiple times, provide more context or set occurrence, line_hint, "
            "replace_all, and expected_replacements. Shows closest-match "
            "diagnostics on failure."
        )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """Strip trailing whitespace from each line."""
        return "\n".join(line.rstrip() for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, occurrence: int | None = None,
        line_hint: int | None = None, expected_replacements: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if old_text is None:
                raise ValueError("Unknown old_text")
            if new_text is None:
                raise ValueError("Unknown new_text")
            if occurrence is not None and occurrence < 1:
                return "Error: occurrence must be >= 1."
            if line_hint is not None and line_hint < 1:
                return "Error: line_hint must be >= 1."
            if expected_replacements is not None and expected_replacements < 1:
                return "Error: expected_replacements must be >= 1."

            fp = self._resolve_write(path)

            # Create-file semantics: old_text='' + file doesn't exist → create
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    self._file_states.record_write(fp)
                    return f"Successfully created {fp}"
                return self._file_not_found_msg(path, fp)

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB."

            # Create-file: old_text='' but file exists and not empty → reject
            if old_text == "":
                raw = fp.read_bytes()
                content = raw.decode("utf-8")
                if content.strip():
                    return f"Error: Cannot create file — {path} already exists and is not empty."
                fp.write_text(new_text, encoding="utf-8")
                self._file_states.record_write(fp)
                return f"Successfully edited {fp}"

            # Read-before-edit check
            warning = self._file_states.check_read(fp)

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if replace_all and occurrence is not None:
                return "Error: occurrence cannot be used with replace_all=true."
            if replace_all and line_hint is not None:
                return "Error: line_hint cannot be used with replace_all=true."
            if occurrence is not None and line_hint is not None:
                return "Error: line_hint cannot be used with occurrence."
            if count > 1 and not replace_all:
                if occurrence is not None:
                    if occurrence > count:
                        return (
                            f"Error: occurrence {occurrence} is out of range; "
                            f"old_text appears {count} times."
                        )
                elif line_hint is not None:
                    nearest = min(matches, key=lambda match: abs(match.line - line_hint))
                    distance = abs(nearest.line - line_hint)
                    if sum(1 for match in matches if abs(match.line - line_hint) == distance) > 1:
                        return (
                            f"Error: line_hint {line_hint} is ambiguous; "
                            f"old_text appears {count} times."
                        )
                else:
                    line_numbers = [match.line for match in matches]
                    preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                    if len(line_numbers) > 3:
                        preview += ", ..."
                    location_hint = f" at {preview}" if preview else ""
                    return (
                        f"Warning: old_text appears {count} times{location_hint}. "
                        "Provide more context, set occurrence to choose one match, "
                        "or set replace_all=true."
                    )
            elif occurrence is not None and occurrence > count:
                return (
                    f"Error: occurrence {occurrence} is out of range; "
                    f"old_text appears {count} time."
                )

            norm_new = new_text.replace("\r\n", "\n")

            # Trailing whitespace stripping (skip markdown to preserve double-space line breaks)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            if replace_all:
                selected = matches
            elif line_hint is not None:
                selected = [min(matches, key=lambda match: abs(match.line - line_hint))]
            else:
                selected = [matches[occurrence - 1 if occurrence else 0]]
            if expected_replacements is not None and len(selected) != expected_replacements:
                return (
                    f"Error: expected {expected_replacements} replacements but "
                    f"would make {len(selected)}."
                )
            new_content = content
            for match in reversed(selected):
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing
                # newline to avoid leaving a blank line
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            self._file_states.record_write(fp)
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            return msg
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """Build an error message with 'Did you mean ...?' suggestions."""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return "\n".join(parts)

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return (
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return (
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            200,
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""
    _scopes = {"core", "orchestrator", "subagent"}

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if not items and total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"


# Registrazione esplicita dei tool di questo modulo (Fase 5.3): il
# ToolLoader legge questa lista invece della reflection dir(). Un nuovo
# tool va aggiunto qui esplicitamente.
TOOLS = [ReadFileTool, WriteFileTool, EditFileTool, ListDirTool]
