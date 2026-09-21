"""Describe a staged skill's supporting files so the agent can read them efficiently."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILL_MANIFEST_NAME = "SKILL.md"
SKILL_FILE_INVENTORY_HEADING = "## Skill files"
MAX_INVENTORY_ENTRIES = 200
# Claude Code's Read tool returns at most this many lines per call unless the
# caller passes `offset`/`limit`.
READ_TOOL_DEFAULT_LINE_LIMIT = 2000
# Claude Code's Read tool refuses PDFs above this page count unless `pages` is set.
READ_TOOL_PDF_PAGE_THRESHOLD = 10
READ_TOOL_PDF_MAX_PAGES_PER_REQUEST = 20
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_TEXT_SNIFF_BYTES = 8192

_PDF_GUIDANCE = (
    "PDFs with more than "
    f"{READ_TOOL_PDF_PAGE_THRESHOLD} pages cannot be read in one call: pass "
    f'`pages` (for example `pages: "1-{READ_TOOL_PDF_PAGE_THRESHOLD}"`, at most '
    f"{READ_TOOL_PDF_MAX_PAGES_PER_REQUEST} pages per call), or extract the text "
    "once with `pdftotext -layout <file> -` in Bash and grep the output."
)
_LARGE_TEXT_GUIDANCE = (
    f"Files longer than {READ_TOOL_DEFAULT_LINE_LIMIT} lines are truncated by a "
    "plain Read: pass `offset` and `limit`, or Grep for the relevant section "
    "instead of reading the whole file."
)


@dataclass(frozen=True, slots=True)
class SkillFileEntry:
    """One supporting file inside a staged skill directory."""

    relative_path: str
    size_bytes: int
    kind: str
    line_count: int | None = None

    def render(self) -> str:
        details = [self.kind, format_size(self.size_bytes)]
        if self.line_count is not None:
            details.append(f"{self.line_count} lines")
        line = f"- `{self.relative_path}` ({', '.join(details)})"
        if self.kind == "pdf":
            line += ": read with `pages` or `pdftotext`"
        elif (
            self.line_count is not None
            and self.line_count > READ_TOOL_DEFAULT_LINE_LIMIT
        ):
            line += ": read with `offset`/`limit` or Grep"
        return line


def format_size(size_bytes: int) -> str:
    """Render a byte count in the closest binary unit."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    value = size_bytes / 1024
    for unit in ("KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _count_text_lines(path: Path) -> int | None:
    """Return the line count when the file is UTF-8 text, otherwise None."""
    with path.open("rb") as handle:
        head = handle.read(_TEXT_SNIFF_BYTES)
        if b"\x00" in head:
            return None
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multi-byte sequence may straddle the sniff window.
            if exc.end < len(head) - 4:
                return None
        handle.seek(0)
        lines = 0
        last_byte = b""
        while chunk := handle.read(1024 * 1024):
            lines += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if last_byte and last_byte != b"\n":
        lines += 1
    return lines


def inspect_skill_file(skill_dir: Path, path: Path) -> SkillFileEntry:
    """Classify one supporting file for the inventory."""
    relative_path = path.relative_to(skill_dir).as_posix()
    size_bytes = path.stat().st_size
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return SkillFileEntry(relative_path, size_bytes, "pdf")
    if suffix in _IMAGE_SUFFIXES:
        return SkillFileEntry(relative_path, size_bytes, "image")
    line_count = _count_text_lines(path)
    if line_count is None:
        return SkillFileEntry(relative_path, size_bytes, "binary")
    return SkillFileEntry(relative_path, size_bytes, "text", line_count=line_count)


def collect_skill_files(skill_dir: Path) -> list[SkillFileEntry]:
    """List every supporting file under the skill, excluding the root manifest."""
    entries: list[SkillFileEntry] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.parent == skill_dir and path.name == SKILL_MANIFEST_NAME:
            continue
        entries.append(inspect_skill_file(skill_dir, path))
    return entries


def render_skill_file_inventory(entries: list[SkillFileEntry]) -> str | None:
    """Render the inventory section, or None when there is nothing to describe."""
    if not entries:
        return None
    shown = entries[:MAX_INVENTORY_ENTRIES]
    lines = [
        SKILL_FILE_INVENTORY_HEADING,
        "",
        "Supporting files bundled with this skill, relative to the skill "
        "directory. Use these sizes to choose how to read each file before "
        "calling Read.",
        "",
        *(entry.render() for entry in shown),
    ]
    if len(entries) > len(shown):
        lines.append(f"- ... and {len(entries) - len(shown)} more files")
    guidance: list[str] = []
    if any(entry.kind == "pdf" for entry in entries):
        guidance.append(_PDF_GUIDANCE)
    if any(
        entry.line_count is not None and entry.line_count > READ_TOOL_DEFAULT_LINE_LIMIT
        for entry in entries
    ):
        guidance.append(_LARGE_TEXT_GUIDANCE)
    if guidance:
        lines.append("")
        lines.extend(f"- {item}" for item in guidance)
    return "\n".join(lines)


def append_skill_file_inventory(skill_dir: Path) -> bool:
    """Append the supporting-file inventory to a staged skill's root SKILL.md.

    Runs against the per-run staged copy, never the shared cache, so the
    published skill content is left untouched.

    Returns:
        True when an inventory section was written.
    """
    manifest = skill_dir / SKILL_MANIFEST_NAME
    if not manifest.is_file():
        return False
    inventory = render_skill_file_inventory(collect_skill_files(skill_dir))
    if inventory is None:
        return False
    body = manifest.read_text(encoding="utf-8")
    separator = "" if body.endswith("\n") else "\n"
    manifest.write_text(f"{body}{separator}\n{inventory}\n", encoding="utf-8")
    return True
