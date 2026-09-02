#!/usr/bin/env python3
"""Keep each pull request in one release-notes section, the highest-ranked one.

Release Drafter lists a pull request under *every* category whose labels it
matches. Its own source says so:

    // note that having the same label in multiple categories
    // then it is intended to "duplicate" the pull request into each category

That is not what this repo wants. A reader should meet each change once, in the
section that best describes it, and `.github/release-drafter.yml` orders its
categories by exactly that ranking: act-on-this first, then product areas, then
by kind. There is no per-category `exclude-labels` in the action's schema and no
first-match option, so the ranking has to be applied after the draft is written.

Release Drafter emits sections in the order the config declares them, so the
first occurrence of a pull request is the highest-ranked one. This keeps that
occurrence and drops the rest, then removes any heading left with nothing under
it.

Reads a release body on stdin, writes the deduplicated body on stdout, and is a
no-op on a body that is already clean. Runs on the standard library alone: the
job that calls it installs nothing.
"""

from __future__ import annotations

import re
import sys
from typing import Final

# A rendered change line always ends in its pull request number; that number is
# the identity we deduplicate on, not the text, which differs between sections
# only by accident.
CHANGE_LINE: Final = re.compile(r"^- .*\(#(?P<number>\d+)\)\s*$")
HEADING: Final = re.compile(r"^#{2,6} \S")
# The changelog link ends the sections; everything from it on is a document
# trailer rather than the content of whichever section happens to precede it.
TRAILER: Final = re.compile(r"^\*\*Full [Cc]hangelog\*\*")


def dedupe(body: str) -> str:
    """Return `body` with every repeated pull request dropped after its first."""
    seen: set[str] = set()
    kept: list[str] = []

    for line in body.splitlines():
        match = CHANGE_LINE.match(line)
        if match is None:
            kept.append(line)
            continue
        number = match.group("number")
        if number in seen:
            continue
        seen.add(number)
        kept.append(line)

    return _drop_empty_sections(kept)


def _drop_empty_sections(lines: list[str]) -> str:
    """Remove a heading whose entries were all taken by a higher-ranked section.

    The changelog link is a document trailer, not the content of whichever
    section happens to precede it, so it is held back before the scan. Without
    that, the last section always looks occupied and never gets removed.
    """
    end = next((i for i, line in enumerate(lines) if TRAILER.match(line)), len(lines))
    body, trailer = lines[:end], lines[end:]

    keep = [True] * len(body)
    for i, line in enumerate(body):
        if not HEADING.match(line):
            continue
        following = next(
            (body[j] for j in range(i + 1, len(body)) if body[j].strip()), None
        )
        if following is None or HEADING.match(following):
            keep[i] = False

    out = [line for i, line in enumerate(body) if keep[i]] + trailer
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def main() -> int:
    sys.stdout.write(dedupe(sys.stdin.read().replace("\r\n", "\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
