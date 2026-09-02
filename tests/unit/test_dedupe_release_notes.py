"""Each pull request appears once, in the highest-ranked section that claimed it.

Release Drafter duplicates a pull request into every category its labels match.
`.github/release-drafter.yml` orders categories by rank and the action emits
them in that order, so keeping the first occurrence is the ranking.
"""

from __future__ import annotations

import pytest
from dedupe_release_notes import dedupe

FOOTER = "**Full changelog**: https://github.com/TracecatHQ/tracecat/compare/a...b"


def test_a_pull_request_is_kept_in_the_first_section_that_claimed_it() -> None:
    body = "\n".join(
        (
            "## Security",
            "",
            "- fix(audit): Preserve client attribution (#3362)",
            "",
            "## API",
            "",
            "- fix(audit): Preserve client attribution (#3362)",
            "- fix(api): Return adjacent reverse pages (#3258)",
            "",
            FOOTER,
            "",
        )
    )
    assert dedupe(body) == "\n".join(
        (
            "## Security",
            "",
            "- fix(audit): Preserve client attribution (#3362)",
            "",
            "## API",
            "",
            "- fix(api): Return adjacent reverse pages (#3258)",
            "",
            FOOTER,
            "",
        )
    )


def test_a_section_left_with_nothing_is_removed() -> None:
    body = "\n".join(
        (
            "## Case management",
            "",
            "- feat(cases): Add duplication (#1)",
            "",
            "## User interface",
            "",
            "- feat(cases): Add duplication (#1)",
            "",
            "## Fixes",
            "",
            "- fix(ui): Correct a label (#2)",
            "",
            FOOTER,
            "",
        )
    )
    out = dedupe(body)
    assert "## User interface" not in out
    assert "## Case management" in out and "## Fixes" in out
    assert out.count("(#1)") == 1


def test_a_clean_body_is_unchanged() -> None:
    body = "\n".join(
        ("## Fixes", "", "- fix(ui): Correct a label (#2)", "", FOOTER, "")
    )
    assert dedupe(body) == body


def test_it_is_idempotent() -> None:
    body = "\n".join(
        (
            "## Security",
            "",
            "- fix(audit): x (#1)",
            "",
            "## Fixes",
            "",
            "- fix(audit): x (#1)",
            "",
            FOOTER,
            "",
        )
    )
    once = dedupe(body)
    assert dedupe(once) == once


def test_trailing_prose_after_the_last_section_survives() -> None:
    """The contributors block is not a heading and must not be treated as one."""
    body = "\n".join(
        (
            "## Fixes",
            "",
            "- fix(ui): x (#1)",
            "",
            "## Contributors",
            "",
            "@someone and @someone-else",
            "",
            FOOTER,
            "",
        )
    )
    out = dedupe(body)
    assert "## Contributors" in out
    assert "@someone and @someone-else" in out
    assert FOOTER in out


def test_lines_without_a_pull_request_number_are_never_dropped() -> None:
    """Hand-written bullets have no number and are not duplicates of each other."""
    body = "\n".join(
        (
            "## Notes",
            "",
            "- Upgrade to Node 18 before installing",
            "- Upgrade to Node 18 before installing",
            "",
            FOOTER,
            "",
        )
    )
    assert dedupe(body).count("Upgrade to Node 18") == 2


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_crlf_bodies_are_handled(newline: str) -> None:
    """GitHub stores release bodies with CRLF, which breaks `$`-anchored matching."""
    body = newline.join(
        ("## Security", "", "- fix: x (#1)", "", "## Fixes", "", "- fix: x (#1)", "")
    )
    out = dedupe(body.replace("\r\n", "\n"))
    assert out.count("(#1)") == 1
    assert "\r" not in out


def test_the_first_section_wins_even_when_the_text_differs() -> None:
    """Identity is the pull request number; the rendered text can differ."""
    body = "\n".join(
        (
            "## Breaking changes",
            "",
            "- feat(api)!: Drop the v1 payload (#9)",
            "",
            "## Features",
            "",
            "- feat(api): Drop the v1 payload (#9)",
            "",
            FOOTER,
            "",
        )
    )
    out = dedupe(body)
    assert "## Breaking changes" in out
    assert "## Features" not in out
    assert out.count("(#9)") == 1
