#!/usr/bin/env python3
# Host-only: Build/maintenance script for vendoring UI assets.
# Downloads from CDN (npm-based). Not used during Android build.
"""Vendorize external CDN assets referenced by jenny/templates/ui/index.html."""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

INDEX_HTML = Path(__file__).resolve().parent.parent / "jenny/templates/ui/index.html"
VENDOR_DIR = INDEX_HTML.parent / "assets/vendor"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

URL_RE = re.compile(r'(?:href|src)=["\']((https?:)?//[^"\']+)["\']', re.IGNORECASE)
CSS_URL_RE = re.compile(r'url\([\'"]?([^\'"\)]+)[\'"]?\)', re.IGNORECASE)
CSS_IMPORT_RE = re.compile(
    r'@import\s+(?:url\([\'"]?([^\'"\)]+)[\'"]?\)|["\']([^"\']+)["\'])',
    re.IGNORECASE,
)

LICENSE_PATHS = {
    "codemirror@5.65.16": "https://cdn.jsdelivr.net/npm/codemirror@5.65.16/LICENSE",
    "@tabler/icons-webfont@3.19.0": "https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.19.0/LICENSE",
    "katex@0.16.10": "https://cdn.jsdelivr.net/npm/katex@0.16.10/LICENSE",
    "marked@15.0.7": "https://cdn.jsdelivr.net/npm/marked@15.0.7/LICENSE.md",
    "mermaid@10": "https://cdn.jsdelivr.net/npm/mermaid@10/LICENSE",
    "d3@7": "https://cdn.jsdelivr.net/npm/d3@7/LICENSE",
    "highlight.js@11.11.1": "https://raw.githubusercontent.com/highlightjs/cdn-release/11.11.1/LICENSE",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def normalize_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url


def local_path_for_url(url: str) -> Path:
    parsed = urlparse(url)
    if "fonts.googleapis.com" in parsed.netloc:
        return Path("fonts/google-fonts.css")
    local = parsed.path.lstrip("/")
    if parsed.netloc == "cdn.jsdelivr.net" and local.startswith("npm/"):
        local = local[4:]
    elif parsed.netloc == "cdn.jsdelivr.net" and local.startswith("gh/"):
        local = local[3:]
        if local.startswith("highlightjs/cdn-release@"):
            local = "highlight.js" + local[len("highlightjs/cdn-release"):]
    if not local:
        raise ValueError(f"Cannot derive local path for {url}")
    last = Path(local).name
    if "@" in last and "." not in last and parsed.netloc == "cdn.jsdelivr.net":
        pkg = last.split("@")[0]
        local = f"{local}/{pkg}.min.js"
    return Path(local)


def save_file(rel_path: Path, data: bytes) -> Path:
    dest = VENDOR_DIR / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def rewrite_google_fonts_css(css_bytes: bytes) -> tuple[bytes, list[tuple[str, Path]]]:
    css_text = css_bytes.decode("utf-8")
    downloads: list[tuple[str, Path]] = []

    def repl(match: re.Match) -> str:
        raw = match.group(1)
        url = normalize_url(raw)
        parsed = urlparse(url)
        name = Path(parsed.path).name or "font"
        if parsed.query:
            name += f"_{re.sub(r'[^A-Za-z0-9_.-]', '_', parsed.query)}"
        local = Path("fonts") / name
        downloads.append((url, local))
        return f'url("{name}")'

    rewritten = CSS_URL_RE.sub(repl, css_text)
    return rewritten.encode("utf-8"), downloads


def process_css(css_url: str, css_local: Path, css_bytes: bytes, downloaded: dict[str, Path]) -> None:
    if "fonts.googleapis.com" in css_url:
        css_bytes, extra = rewrite_google_fonts_css(css_bytes)
        save_file(css_local, css_bytes)
        for url, rel in extra:
            if url in downloaded:
                continue
            data = fetch(url)
            dest = save_file(rel, data)
            downloaded[url] = dest
            print(f"  font: {url} -> {dest.relative_to(VENDOR_DIR)}")
        return

    css_text = css_bytes.decode("utf-8", errors="replace")

    def handle_ref(raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("data:") or raw.startswith("#"):
            return raw
        full = normalize_url(urljoin(css_url, raw))
        if full in downloaded:
            return raw
        try:
            rel = local_path_for_url(full)
        except ValueError:
            return raw
        data = fetch(full)
        dest = save_file(rel, data)
        downloaded[full] = dest
        print(f"  subresource: {full} -> {dest.relative_to(VENDOR_DIR)}")
        if dest.suffix == ".css":
            process_css(full, rel, data, downloaded)
        return raw

    for match in CSS_URL_RE.finditer(css_text):
        handle_ref(match.group(1))

    for match in CSS_IMPORT_RE.finditer(css_text):
        imported = match.group(1) or match.group(2)
        if imported:
            full = normalize_url(urljoin(css_url, imported))
            if full in downloaded:
                continue
            rel = local_path_for_url(full)
            data = fetch(full)
            dest = save_file(rel, data)
            downloaded[full] = dest
            print(f"  imported css: {full} -> {dest.relative_to(VENDOR_DIR)}")
            process_css(full, rel, data, downloaded)


def fetch_license(package_key: str, dest_dir: Path) -> None:
    url = LICENSE_PATHS.get(package_key)
    if not url:
        return
    try:
        data = fetch(url)
        (dest_dir / "LICENSE").write_bytes(data)
        print(f"  license: {url} -> {dest_dir.relative_to(VENDOR_DIR)}/LICENSE")
    except Exception as exc:
        print(f"  license skipped for {package_key}: {exc}")


def main() -> int:
    if not INDEX_HTML.exists():
        print(f"Missing {INDEX_HTML}", file=sys.stderr)
        return 1

    html = INDEX_HTML.read_text(encoding="utf-8")
    raw_urls = {normalize_url(u) for u in URL_RE.findall(html) for u in [u[0]]}
    urls = sorted(raw_urls)

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, Path] = {}

    for url in urls:
        print(f"Downloading {url}")
        data = fetch(url)
        rel = local_path_for_url(url)
        dest = save_file(rel, data)
        downloaded[url] = dest
        print(f"  -> {dest.relative_to(VENDOR_DIR)}")

        if dest.suffix == ".css":
            process_css(url, rel, data, downloaded)

    for package_key in LICENSE_PATHS:
        pkg_dir = VENDOR_DIR / package_key
        if pkg_dir.exists() and package_key not in {"fonts"}:
            fetch_license(package_key, pkg_dir)

    print(f"\nVendorized {len(downloaded)} files to {VENDOR_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
