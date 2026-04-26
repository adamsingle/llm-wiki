"""
tools.py — Filesystem tools for the LLM Wiki Agent.

Handles all file I/O for the agent, including reading multiple source
formats (PDF, plain text, markdown, HTML, CSV) and writing wiki pages.
"""

import os
import csv
import datetime
import io
from pathlib import Path
from typing import Optional

# ─── File reading ─────────────────────────────────────────────────────────────

# Map of extensions to reader functions (populated below)
_READERS = {}

def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return (
            "[PDF reading requires pypdf. Install it with:\n"
            "  pip install pypdf\n"
            "Then re-run your ingest command.]"
        )
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i + 1} ---\n{text}")
    if not pages:
        return "[PDF contained no extractable text — it may be a scanned image PDF.]"
    return "\n\n".join(pages)

def _read_html(path: Path) -> str:
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self._skip = False
            if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr"):
                self.parts.append("\n")

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    raw = path.read_text(encoding="utf-8", errors="replace")
    stripper = _Stripper()
    stripper.feed(raw)
    text = "".join(stripper.parts)
    # Collapse excessive blank lines
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _read_csv(path: Path) -> str:
    rows = []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(" | ".join(row))
    return "\n".join(rows)

def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        return (
            "[DOCX reading requires python-docx. Install it with:\n"
            "  pip install python-docx\n"
            "Then re-run your ingest command.]"
        )
    doc = docx.Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)

# Extension registry
_READERS = {
    ".md":   _read_markdown,
    ".txt":  _read_text,
    ".text": _read_text,
    ".pdf":  _read_pdf,
    ".html": _read_html,
    ".htm":  _read_html,
    ".csv":  _read_csv,
    ".tsv":  _read_csv,
    ".docx": _read_docx,
    # Treat these as plain text
    ".rst":  _read_text,
    ".tex":  _read_text,
    ".json": _read_text,
    ".yaml": _read_text,
    ".yml":  _read_text,
    ".xml":  _read_text,
}

MAX_CHARS = 120_000   # ~30k tokens — enough for most sources


def extract_text(path: Path) -> str:
    """
    Extract readable text from a file, regardless of format.
    Returns a plain-text string suitable for passing to an LLM.
    """
    ext = path.suffix.lower()
    reader = _READERS.get(ext)

    if reader is None:
        # Fall back to plain-text read and hope for the best
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return f"[Unknown format '{ext}' — read as plain text]\n\n{content}"
        except Exception as e:
            return f"[Cannot read file '{path.name}': {e}]"

    try:
        content = reader(path)
    except Exception as e:
        return f"[Error reading '{path.name}' as {ext}: {e}]"

    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS] + f"\n\n[... TRUNCATED — file exceeded {MAX_CHARS} chars ...]"

    return content


# ─── WikiTools class ──────────────────────────────────────────────────────────

class WikiTools:
    """
    All filesystem operations available to the wiki agent.
    Reads from anywhere in the wiki root; writes only to wiki/.
    """

    def __init__(self, wiki_root: Path):
        self.wiki_root = wiki_root.resolve()
        self.wiki_dir = self.wiki_root / "wiki"
        self.raw_dir = self.wiki_root / "raw"

    # ── Safety helpers ────────────────────────────────────────────────────────

    def _resolve(self, path_str: str) -> Path:
        p = (self.wiki_root / path_str).resolve()
        if not str(p).startswith(str(self.wiki_root)):
            raise PermissionError(f"Path escape blocked: '{path_str}'")
        return p

    def _resolve_writable(self, path_str: str) -> Path:
        p = self._resolve(path_str)
        if not str(p).startswith(str(self.wiki_dir)):
            raise PermissionError(
                f"Write blocked: '{path_str}' is outside wiki/. "
                "The agent may only write inside the wiki/ directory."
            )
        return p

    # ── Tool: read_file ───────────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        """
        Read any file in the wiki root, extracting text from PDF/DOCX/HTML
        etc. as needed. Returns plain text for the LLM.
        """
        try:
            p = self._resolve(path)
        except PermissionError as e:
            return f"[PERMISSION ERROR: {e}]"

        if not p.exists():
            return f"[FILE NOT FOUND: {path}]"
        if not p.is_file():
            return f"[NOT A FILE: {path}]"

        ext = p.suffix.lower()
        is_wiki_page = str(p).startswith(str(self.wiki_dir)) and ext == ".md"

        if is_wiki_page:
            # Wiki pages are always plain UTF-8 markdown — fast path
            return p.read_text(encoding="utf-8", errors="replace")

        # Source file — extract text based on format
        format_label = ext.lstrip(".").upper() or "UNKNOWN"
        header = f"[Source: {p.name} | Format: {format_label}]\n\n"
        return header + extract_text(p)

    # ── Tool: write_file ──────────────────────────────────────────────────────

    def write_file(self, path: str, content: str) -> str:
        """Create or overwrite a wiki page. Only wiki/ is writable."""
        try:
            p = self._resolve_writable(path)
        except PermissionError as e:
            return f"[PERMISSION ERROR: {e}]"

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"[OK: wrote {len(content)} chars to {path}]"
        except Exception as e:
            return f"[ERROR writing '{path}': {e}]"

    # ── Tool: list_wiki ───────────────────────────────────────────────────────

    def list_wiki(self, subdirectory: Optional[str] = None) -> str:
        """List all markdown files in wiki/ (or a subdirectory)."""
        base = self.wiki_dir
        if subdirectory:
            base = self._resolve(subdirectory)

        if not base.exists():
            return f"[Directory not found: {subdirectory or 'wiki/'}]"

        files = sorted(base.rglob("*.md"))
        if not files:
            return "[No wiki pages found]"

        lines = []
        for f in files:
            rel = str(f.relative_to(self.wiki_root))
            size_kb = f.stat().st_size / 1024
            lines.append(f"{rel}  ({size_kb:.1f} KB)")
        return "\n".join(lines)

    # ── Tool: list_raw_sources ────────────────────────────────────────────────

    def list_raw_sources(self) -> str:
        """List all files in raw/ with their formats."""
        if not self.raw_dir.exists():
            return "[raw/ directory not found]"

        files = sorted(
            f for f in self.raw_dir.iterdir() if f.is_file()
        )
        if not files:
            return "[No source files in raw/ — add files to ingest]"

        lines = []
        for f in files:
            ext = f.suffix.lower().lstrip(".")
            size_kb = f.stat().st_size / 1024
            readable = "✓" if f.suffix.lower() in _READERS else "?"
            lines.append(f"{f.name}  ({ext.upper()}, {size_kb:.1f} KB)  {readable}")

        supported = ", ".join(sorted(set(e.lstrip(".").upper() for e in _READERS)))
        lines.append(f"\nSupported formats: {supported}")
        return "\n".join(lines)

    # ── Tool: search_wiki ─────────────────────────────────────────────────────

    def search_wiki(self, query: str) -> str:
        """Case-insensitive search across all wiki pages."""
        if not self.wiki_dir.exists():
            return "[wiki/ directory not found]"

        query_lower = query.lower()
        results = []

        for md_file in sorted(self.wiki_dir.rglob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            lines = text.splitlines()
            matches = [
                f"  {i+1:4d}: {line.strip()[:120]}"
                for i, line in enumerate(lines)
                if query_lower in line.lower()
            ]
            if matches:
                rel = str(md_file.relative_to(self.wiki_root))
                results.append(f"### {rel}\n" + "\n".join(matches[:6]))
            if len(results) >= 15:
                break

        if not results:
            return f"[No results for '{query}' in wiki/]"
        return f"Search: '{query}'\n\n" + "\n\n".join(results)

    # ── Tool: append_log ──────────────────────────────────────────────────────

    def append_log(self, operation: str, title: str, summary: str) -> str:
        """Append a timestamped entry to wiki/log.md."""
        log_path = self.wiki_dir / "log.md"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"\n## [{now}] {operation} | {title}\n\n"
                f"{summary}\n"
            )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return f"[OK: log entry appended]"
        except Exception as e:
            return f"[ERROR writing log: {e}]"

    # ── Tool: delete_file ─────────────────────────────────────────────────────

    def delete_file(self, path: str) -> str:
        """Delete a wiki page. Only works inside wiki/."""
        try:
            p = self._resolve_writable(path)
        except PermissionError as e:
            return f"[PERMISSION ERROR: {e}]"

        if not p.exists():
            return f"[FILE NOT FOUND: {path}]"
        try:
            p.unlink()
            return f"[OK: deleted {path}]"
        except Exception as e:
            return f"[ERROR deleting '{path}': {e}]"