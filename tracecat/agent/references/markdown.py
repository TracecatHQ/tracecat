"""CommonMark reference extraction. No regex scanning of prose for grants."""

import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath

from markdown_it import MarkdownIt
from markdown_it.rules_inline import autolink, link
from markdown_it.rules_inline.state_inline import StateInline

from tracecat.agent.references.types import (
    ParsedReferences,
    ReferenceDiagnostic,
    ReferenceOccurrence,
    SourceLocation,
)
from tracecat.agent.references.uri import (
    ReferenceDiagnosticCode,
    ReferenceURIError,
    is_reference_uri,
    parse_reference_uri,
)

InlineRule = Callable[[StateInline, bool], bool]


def _located(rule: InlineRule) -> InlineRule:
    def parse(state: StateInline, silent: bool) -> bool:
        start, count = state.pos, len(state.tokens)
        matched = rule(state, silent)
        if matched and not silent:
            for token in state.tokens[count:]:
                if token.type == "link_open":
                    token.meta["reference_offset"] = start
                    break
        return matched

    return parse


def _without_frontmatter(text: str) -> str:
    """Mask YAML without moving source lines; an unclosed header grants nothing."""
    lines = text.splitlines(keepends=True)
    if not lines or not re.fullmatch(r"---[ \t]*\n?", lines[0]):
        return text
    end = next(
        (
            i
            for i in range(1, len(lines))
            if re.fullmatch(r"(?:---|\.\.\.)[ \t]*\n?", lines[i])
        ),
        len(lines) - 1,
    )
    for i in range(end + 1):
        lines[i] = "\n" if lines[i].endswith("\n") else ""
    return "".join(lines)


def _location(
    content: str, offset: int, source: list[str], start: int, path: str
) -> SourceLocation:
    """Map CommonMark's de-indented inline content back to its original line."""
    prefix = content[:offset]
    relative_line = prefix.count("\n")
    column = len(prefix.rsplit("\n", 1)[-1])
    inline_line = content.split("\n")[relative_line]
    line = start + relative_line
    indent = source[line].find(inline_line)
    if indent < 0:
        # CommonMark can expand part of a leading tab in list continuations.
        # Anchor the entire non-indented content, not the link suffix: repeated
        # identical links must retain their distinct columns on the same line.
        content_line = inline_line.lstrip(" \t")
        inline_indent = len(inline_line) - len(content_line)
        anchor = source[line].find(content_line)
        return SourceLocation(
            path, line + 1, max(anchor, 0) + column - inline_indent + 1
        )
    return SourceLocation(path, line + 1, indent + column + 1)


def parse_markdown_references(
    text: str, *, path: str = "instructions.md"
) -> ParsedReferences:
    """Read links, preserving occurrence locations and ignoring inert Markdown.

    Code, images, frontmatter and raw HTML cannot declare capabilities. This
    validates syntax only: workspace authorization belongs to preparation.
    """
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    source = normalized.split("\n")
    parser = MarkdownIt("commonmark")
    parser.inline.ruler.at("link", _located(link))
    parser.inline.ruler.at("autolink", _located(autolink))
    # Keep original reserved destinations for strict URI validation. CommonMark's
    # default URL normalization would hide invalid controls/encoding from us.
    normalize_link = parser.normalizeLink
    parser.normalizeLink = lambda url: (
        url if is_reference_uri(url) else normalize_link(url)
    )
    references: list[ReferenceOccurrence] = []
    diagnostics: list[ReferenceDiagnostic] = []
    for block in parser.parse(_without_frontmatter(normalized)):
        if block.type != "inline" or not block.children or block.map is None:
            continue
        html_tags: list[str] = []
        for token in block.children:
            if token.type == "html_inline":
                tag_match = re.match(
                    r"</?([A-Za-z][A-Za-z0-9-]*)(?=[\s/>])", token.content
                )
                if tag_match is None:
                    continue
                tag = tag_match.group(1).lower()
                if token.content.startswith("</"):
                    if html_tags and html_tags[-1] == tag:
                        html_tags.pop()
                elif tag not in {
                    "area",
                    "base",
                    "br",
                    "col",
                    "embed",
                    "hr",
                    "img",
                    "input",
                    "link",
                    "meta",
                    "param",
                    "source",
                    "track",
                    "wbr",
                } and not token.content.endswith("/>"):
                    html_tags.append(tag)
                continue
            if token.type != "link_open" or html_tags:
                continue
            destination = token.attrGet("href")
            if not isinstance(destination, str):
                continue
            if not is_reference_uri(destination):
                continue
            offset = token.meta.get("reference_offset", 0)
            location = _location(block.content, int(offset), source, block.map[0], path)
            try:
                target = parse_reference_uri(destination)
            except ReferenceURIError as exc:
                diagnostics.append(ReferenceDiagnostic(exc.code, location))
            else:
                references.append(ReferenceOccurrence(target, location))
    return ParsedReferences(tuple(references), tuple(diagnostics))


def is_markdown_path(path: str) -> bool:
    """Use the same Markdown source extensions for scanning and materialization."""
    return PurePosixPath(path).suffix.lower() in {".md", ".markdown"}


def scan_markdown_files(files: Mapping[str, bytes]) -> ParsedReferences:
    """Scan every text Markdown manifest member, including supporting files."""
    references: list[ReferenceOccurrence] = []
    diagnostics: list[ReferenceDiagnostic] = []
    for path, content in sorted(files.items()):
        if not is_markdown_path(path):
            continue
        try:
            text = content.decode("utf-8")
            if "\x00" in text:
                raise ValueError("binary content")
        except (UnicodeDecodeError, ValueError):
            diagnostics.append(
                ReferenceDiagnostic(
                    ReferenceDiagnosticCode.INVALID_SOURCE, SourceLocation(path, 1, 1)
                )
            )
            continue
        result = parse_markdown_references(text, path=path)
        references.extend(result.references)
        diagnostics.extend(result.diagnostics)
    return ParsedReferences(tuple(references), tuple(diagnostics))
