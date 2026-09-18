"""Dispatch a rebuild only when the tag carries the reviewed image publisher.

Run from a trusted, updated main checkout. Historical tags are immutable, so
reject a different workflow instead of running its older publishing policy.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/build-push-images.yml"
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
TAG = re.compile(
    rf"(?:{VERSION}(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?|nightly-[0-9A-Za-z][0-9A-Za-z.-]*)"
)


def run(*args: str) -> str:
    return subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def rebuild(tag: str) -> None:
    if len(tag) > 128 or TAG.fullmatch(tag) is None:
        raise ValueError(
            "Expected an existing version or nightly tag, not a branch or SHA."
        )

    # Read committed policy, not an uncommitted local workflow modification.
    expected = run("git", "show", f"HEAD:{WORKFLOW}")
    repo = run(
        "gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
    ).strip()
    tagged = run(
        "gh",
        "api",
        f"repos/{repo}/contents/{WORKFLOW}?ref={quote(f'refs/tags/{tag}', safe='')}",
        "-H",
        "Accept: application/vnd.github.raw+json",
    )
    if not expected.strip() or tagged != expected:
        raise ValueError(
            "Rebuild refused: the tagged workflow differs from the reviewed publisher "
            "in this checkout. Historical workflows may overwrite stable latest images. "
            "Do not dispatch directly, rerun the historical workflow, or move the tag."
        )

    run(
        "gh",
        "workflow",
        "run",
        Path(WORKFLOW).name,
        "--repo",
        repo,
        "--ref",
        tag,
        "--field",
        f"tag={tag}",
    )
    print(f"Dispatched image rebuild for {repo} tag {tag}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    try:
        rebuild(args.tag)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Rebuild failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
