"""Point main's stable install references at the latest stable release.

Stable releases are cut on ``release/*`` branches, so their version bumps never
reach ``main``. ``main`` still serves the docs and the default Compose and
Fargate image tags, so this rewrites only those stable references. It leaves
``__version__`` and unrelated version strings (for example Terraform
``required_version`` constraints) untouched, unlike ``update-version.sh``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

STABLE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
ANY_VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
STABLE_RE = re.compile(rf"^{STABLE}$")
CURRENT_FILE = "docker-compose.yml"
CURRENT_RE = re.compile(rf"\$\{{TRACECAT__IMAGE_TAG:-({ANY_VERSION})\}}")
PATHSPECS = (
    ":(glob)**/docker-compose*.yml",
    "docs",
    "deployments",
    ".github/ISSUE_TEMPLATE/bug_report.md",
)
SUBSTITUTIONS = (
    (re.compile(rf"(\$\{{TRACECAT__IMAGE_TAG:-){ANY_VERSION}(\}})"), r"\g<1>{v}\g<2>"),
    (
        re.compile(
            rf"(raw\.githubusercontent\.com/TracecatHQ/tracecat/){ANY_VERSION}/"
        ),
        r"\g<1>{v}/",
    ),
    (re.compile(rf"(TF_VAR_tracecat_image_tag=){ANY_VERSION}\b"), r"\g<1>{v}"),
    (
        re.compile(
            r'(variable "tracecat_image_tag" \{[^}]*?default\s*=\s*")[^"]*(")',
            re.DOTALL,
        ),
        r"\g<1>{v}\g<2>",
    ),
    (re.compile(rf"(Tracecat version \(e\.g\. `){ANY_VERSION}(`\))"), r"\g<1>{v}\g<2>"),
    (re.compile(rf"(`>){STABLE}(-beta\.xx`)"), r"\g<1>{v}\g<2>"),
)


def parse_stable(version: str) -> tuple[int, int, int]:
    if not STABLE_RE.fullmatch(version):
        raise ValueError(f"Not a stable release version: {version!r}")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def current_version(root: Path) -> str:
    match = CURRENT_RE.search((root / CURRENT_FILE).read_text())
    if match is None:
        raise ValueError(f"No TRACECAT__IMAGE_TAG default in {CURRENT_FILE}")
    return match.group(1)


def is_newer(version: str, current: str) -> bool:
    core = re.match(STABLE, current)
    if core is None:
        return True
    current_key = parse_stable(core.group(0))
    # A prerelease pin such as 1.3.0-alpha.1 sorts before 1.3.0.
    if core.group(0) != current:
        return parse_stable(version) >= current_key
    return parse_stable(version) > current_key


def tracked_files(root: Path) -> list[Path]:
    # Only regular tracked files: symlinks would be replaced by sed-style writes,
    # and untracked nested checkouts are not ours to edit.
    out = subprocess.run(
        ["git", "ls-files", "-s", "-z", "--", *PATHSPECS],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode()
    files = []
    for entry in filter(None, out.split("\0")):
        mode, _, rest = entry.partition(" ")
        if mode in {"100644", "100755"}:
            files.append(root / rest.split("\t", 1)[1])
    return files


def sync(root: Path, version: str) -> list[Path]:
    parse_stable(version)
    updates: dict[Path, str] = {}
    for path in tracked_files(root):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        updated = text
        for pattern, replacement in SUBSTITUTIONS:
            updated = pattern.sub(replacement.format(v=version), updated)
        if updated != text:
            updates[path] = updated
    # Write only after every file was read so a failure leaves the tree clean.
    for path, updated in updates.items():
        path.write_text(updated)
    return [path.relative_to(root) for path in updates]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Stable release version, e.g. 1.2.3")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    try:
        parse_stable(args.version)
        current = current_version(args.root)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not is_newer(args.version, current):
        print(
            f"main already pins {current}; not moving to {args.version}",
            file=sys.stderr,
        )
        return 0
    for path in sync(args.root, args.version):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
