"""Sync the bundled skill library from pinned upstream GitHub sources.

Upstream entries in ``tracecat/agent/skill/library/sources.toml`` pin repository
commits and list the skills taken from them. Local entries are never downloaded
or overwritten. This script copies each upstream skill
directory, plus the repository's license and notice files, into
``skills/<slug>/`` and records a digest per source. Review the resulting diff
like any dependency bump: agents follow skill text as instructions.

Usage:
    just skill-library add OWNER/REPO PATH... [--group NAME [--summary TEXT --description TEXT]] [--exclude GLOB ...]
    just skill-library sync [GROUP_OR_REPO ...]
    just skill-library update [GROUP_OR_REPO ...]
    just skill-library check

Set ``GITHUB_TOKEN`` to raise GitHub API rate limits for ``add`` and ``update``.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from tracecat.agent.skill.frontmatter import parse_skill_markdown
from tracecat.agent.skill.library.catalog import (
    LIBRARY_ROOT,
    load_library_from,
    validate_library_skill,
)
from tracecat.agent.skill.library.sources import (
    SOURCES_PATH,
    load_sources,
    parse_sources,
    tree_digest,
    verify_library,
)
from tracecat.agent.skill.library.types import LibrarySource, LibrarySourceEntry

MANIFEST_HEADER = (
    "# Bundled skill library: platform groups, local skills, and upstream pins.\n"
    "# Managed by `just skill-library`; sync and update rewrite upstream skills only.\n"
)
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
NOTICE_NAMES = ("NOTICE", "NOTICE.md", "NOTICE.txt")
_TIMEOUT_SECONDS = 60
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _get(url: str) -> bytes:
    headers = {"User-Agent": "tracecat-skill-library"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return response.read()


def _api(path: str) -> object:
    return json.loads(_get(f"https://api.github.com/{path}"))


def default_head(repo: str) -> str:
    """Return the commit at the head of ``repo``'s default branch."""

    match _api(f"repos/{repo}"):
        case {"default_branch": str(branch)}:
            return resolve_commit(repo, branch)
        case _:
            raise ValueError(f"{repo}: could not read the default branch")


def resolve_commit(repo: str, ref: str) -> str:
    """Return the full commit SHA that ``ref`` names in ``repo``."""

    match _api(f"repos/{repo}/commits/{urllib.parse.quote(ref, safe='')}"):
        case {"sha": str(sha)}:
            return sha
        case _:
            raise ValueError(f"{repo}: could not resolve {ref!r} to a commit")


def repo_license(repo: str) -> str:
    """Return the SPDX identifier GitHub detected for ``repo``."""

    match _api(f"repos/{repo}"):
        case {"license": {"spdx_id": str(spdx)}} if spdx != "NOASSERTION":
            return spdx
        case _:
            raise ValueError(f"{repo}: no detectable license; pass --license")


def download(repo: str, commit: str) -> bytes:
    """Fetch the repository archive at ``commit``."""

    return _get(f"https://codeload.github.com/{repo}/tar.gz/{commit}")


def extract(archive: bytes, source: LibrarySource) -> dict[str, dict[str, bytes]]:
    """Return each skill's files from a repository archive, license included.

    Raises:
        ValueError: If a skill has links, unsafe paths, or no ``SKILL.md``, or
            the repository ships no license file.
    """

    roots = {slug: PurePosixPath(path) for slug, path in source.skills.items()}
    files: dict[str, dict[str, bytes]] = {slug: {} for slug in roots}
    top_level: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            # GitHub archives wrap everything in one "<repo>-<sha>/" directory.
            parts = PurePosixPath(member.name).parts[1:]
            if not parts:
                continue
            path = PurePosixPath(*parts)
            owner = next(
                (slug for slug, root in roots.items() if path.is_relative_to(root)),
                None,
            )
            if member.issym() or member.islnk():
                if owner is not None:
                    raise ValueError(f"{owner}: {path} is a link")
                continue
            if not member.isfile() or (owner is None and len(parts) > 1):
                continue
            handle = tar.extractfile(member)
            assert handle is not None
            data = handle.read()
            if len(parts) == 1:
                top_level[path.name] = data
            if owner is None:
                continue
            relative = path.relative_to(roots[owner])
            if ".." in relative.parts:
                raise ValueError(f"{owner}: {path} escapes the skill")
            name = relative.as_posix()
            if any(fnmatch.fnmatch(name, pattern) for pattern in source.exclude):
                continue
            files[owner][name] = data
    for slug, skill_files in files.items():
        if "SKILL.md" not in skill_files:
            raise ValueError(f"{slug}: {source.repo}/{roots[slug]} has no SKILL.md")
        for names, required in ((LICENSE_NAMES, True), (NOTICE_NAMES, False)):
            if any(name in skill_files for name in names):
                continue
            found = next((name for name in names if name in top_level), None)
            if found is not None:
                skill_files[found] = top_level[found]
            elif required:
                raise ValueError(
                    f"{source.repo}: no license file at the repository root"
                )
    return files


def write_skill(root: Path, slug: str, files: Mapping[str, bytes]) -> None:
    """Replace ``root/slug`` with exactly ``files``."""

    destination = root / slug
    if destination.is_symlink():
        raise ValueError(f"{slug}: {destination} is a symlink")
    if destination.exists():
        shutil.rmtree(destination)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _key(value: str) -> str:
    # TOML bare keys are ASCII only; quote anything else.
    bare = value.replace("-", "").replace("_", "")
    return value if bare.isascii() and bare.isalnum() else json.dumps(value)


def render_sources(sources: Sequence[LibrarySourceEntry]) -> str:
    """Render the manifest in its canonical form."""

    # JSON string escapes are valid TOML basic strings.
    lines = [MANIFEST_HEADER.rstrip("\n")]
    groups = {
        source.group: (source.group_summary, source.group_description)
        for source in sources
        if source.group is not None
    }
    for name in sorted(groups):
        summary, description = groups[name]
        lines += ["", f"[groups.{_key(name)}]"]
        if summary is not None:
            lines.append(f"summary = {json.dumps(summary)}")
        if description is not None:
            lines.append(f"description = {json.dumps(description)}")
    # Official sources sort first within their group; standalone skills last.
    for source in sorted(
        sources, key=lambda s: (s.group is None, s.group or "", s.provider or "")
    ):
        lines += ["", "[[source]]", f"kind = {json.dumps(source.kind)}"]
        if source.group is not None:
            lines.append(f"group = {json.dumps(source.group)}")
        if source.provider is not None:
            lines.append(f"provider = {json.dumps(source.provider)}")
        if isinstance(source, LibrarySource):
            lines += [
                f"repo = {json.dumps(source.repo)}",
                f"commit = {json.dumps(source.commit)}",
            ]
        lines.append(f"license = {json.dumps(source.license)}")
        if isinstance(source, LibrarySource):
            if source.exclude:
                patterns = ", ".join(json.dumps(pattern) for pattern in source.exclude)
                lines.append(f"exclude = [{patterns}]")
            lines.append(f"tree_sha256 = {json.dumps(source.tree_sha256)}")
        lines += [
            "",
            "[source.skills]",
        ]
        lines += [
            f"{_key(slug)} = {json.dumps(source.skills[slug])}"
            for slug in sorted(source.skills)
        ]
        if source.summaries:
            lines += ["", "[source.summaries]"]
            lines += [
                f"{_key(slug)} = {json.dumps(source.summaries[slug])}"
                for slug in sorted(source.summaries)
            ]
    return "\n".join(lines) + "\n"


def _save(sources: Sequence[LibrarySourceEntry]) -> None:
    SOURCES_PATH.write_text(render_sources(sources), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _PreparedSync:
    source: LibrarySource
    skills: Mapping[str, Mapping[str, bytes]]


def _prepare_sync(source: LibrarySource, archive: bytes) -> _PreparedSync:
    skills = extract(archive, source)
    for slug, files in skills.items():
        validate_library_skill(slug, files)
    synced = {
        f"{slug}/{name}": data
        for slug, files in skills.items()
        for name, data in files.items()
    }
    return _PreparedSync(replace(source, tree_sha256=tree_digest(synced)), skills)


def _apply_sync(prepared: _PreparedSync) -> None:
    for slug, files in prepared.skills.items():
        write_skill(LIBRARY_ROOT, slug, files)


def _select(sources: Sequence[LibrarySourceEntry], keys: Sequence[str]) -> list[int]:
    """Select upstream sources by group or repo; local sources are never synced."""

    if not keys:
        return [
            i for i, source in enumerate(sources) if isinstance(source, LibrarySource)
        ]
    wanted = {key.lower() for key in keys}
    found: list[int] = []
    matched: set[str] = set()
    for i, source in enumerate(sources):
        names = {name.lower() for name in (source.group, source.provider) if name}
        if isinstance(source, LibrarySource):
            names.add(source.repo.lower())
        matches = names & wanted
        matched.update(matches)
        if matches and isinstance(source, LibrarySource):
            found.append(i)
    if unknown := sorted(wanted - matched):
        raise SystemExit(f"Unknown library sources: {', '.join(unknown)}")
    return found


def cmd_sync(keys: Sequence[str]) -> None:
    sources = load_sources()
    prepared: list[_PreparedSync] = []
    for i in _select(sources, keys):
        source = sources[i]
        assert isinstance(source, LibrarySource)
        candidate = _prepare_sync(source, download(source.repo, source.commit))
        sources[i] = candidate.source
        prepared.append(candidate)
    # Validate every selected source before changing any live directory.
    for candidate in prepared:
        _apply_sync(candidate)
        source = candidate.source
        print(
            f"synced {source.group or source.provider} from {source.repo}@{source.commit[:12]}"
        )
    if not keys and LIBRARY_ROOT.is_dir():
        listed = {slug for source in sources for slug in source.skills}
        for orphan in LIBRARY_ROOT.iterdir():
            if orphan.is_dir() and orphan.name not in listed:
                shutil.rmtree(orphan)
                print(f"removed {orphan.name}: not in sources.toml")
    _save(sources)


def cmd_update(keys: Sequence[str]) -> None:
    sources = load_sources()
    prepared: list[tuple[LibrarySource, _PreparedSync]] = []
    for i in _select(sources, keys):
        source = sources[i]
        assert isinstance(source, LibrarySource)
        current = (
            f"{source.group or source.provider} is current at {source.commit[:12]}"
        )
        commit = default_head(source.repo)
        if commit == source.commit:
            print(current)
            continue
        candidate = _prepare_sync(
            replace(source, commit=commit), download(source.repo, commit)
        )
        # Keep the pin when newer commits leave these skills untouched.
        if candidate.source.tree_sha256 == source.tree_sha256:
            print(current)
            continue
        sources[i] = candidate.source
        prepared.append((source, candidate))
    for source, candidate in prepared:
        _apply_sync(candidate)
        print(
            f"updated {source.group or source.provider}: https://github.com/{source.repo}/compare/"
            f"{source.commit}...{candidate.source.commit}"
        )
    _save(sources)


def cmd_add(
    repo: str,
    paths: Sequence[str],
    group: str | None,
    exclude: Sequence[str],
    commit: str | None,
    license_id: str | None,
    description: str | None = None,
    summary: str | None = None,
) -> None:
    sources = load_sources()
    paths = [path.strip("/") for path in paths]
    if commit is not None and not _FULL_SHA.fullmatch(commit):
        commit = resolve_commit(repo, commit)
    existing = next(
        (
            i
            for i, s in enumerate(sources)
            if isinstance(s, LibrarySource) and s.repo == repo
        ),
        None,
    )
    if existing is None:
        if group is None:
            raise SystemExit(f"{repo} is a new source; pass --group")
        known = next((s for s in sources if s.group == group), None)
        description = description or (known and known.group_description)
        summary = summary or (known and known.group_summary)
        if not description or not summary:
            raise SystemExit(
                f"{group} is a new group; pass --summary and --description"
            )
        base = LibrarySource(
            group=group,
            group_summary=summary,
            group_description=description,
            provider=None,
            repo=repo,
            commit=commit or default_head(repo),
            license=license_id or repo_license(repo),
            skills={},
            tree_sha256="",
            exclude=tuple(exclude),
        )
    else:
        base = sources[existing]
        assert isinstance(base, LibrarySource)
        if commit and commit != base.commit:
            raise SystemExit(f"{repo} is pinned at {base.commit[:12]}; use update")
        if group and group != base.group:
            raise SystemExit(f"{repo} belongs to {base.group}, not {group}")
        if base.group is None and (len(paths) != 1 or not summary):
            raise SystemExit(
                f"{repo} lists standalone skills; add one PATH with --summary"
            )
        base = replace(base, exclude=tuple(dict.fromkeys((*base.exclude, *exclude))))
    archive = download(repo, base.commit)
    # Key new skills by path until their SKILL.md names them.
    found = extract(archive, replace(base, skills={path: path for path in paths}))
    listed = {slug for source in sources for slug in source.skills}
    added: dict[str, str] = {}
    for path in paths:
        frontmatter = parse_skill_markdown(found[path]["SKILL.md"].decode("utf-8"))
        if frontmatter is None:
            raise SystemExit(f"{repo}/{path}/SKILL.md has no frontmatter")
        if frontmatter.name in listed or frontmatter.name in added:
            raise SystemExit(f"{frontmatter.name} is already in sources.toml")
        added[frontmatter.name] = path
    summaries = dict(base.summaries)
    if base.group is None and summary:
        summaries.update(dict.fromkeys(added, summary))
    candidate = _prepare_sync(
        replace(base, skills={**base.skills, **added}, summaries=summaries), archive
    )
    synced = candidate.source
    if existing is None:
        sources.append(synced)
    else:
        sources[existing] = synced
    # Reject a manifest the library cannot load before touching skill directories.
    parse_sources(render_sources(sources))
    _apply_sync(candidate)
    _save(sources)
    print(
        f"added {', '.join(sorted(added))} from {repo}@{synced.commit[:12]} "
        f"({synced.group or synced.provider}, {synced.license})"
    )


def cmd_check() -> None:
    if problems := verify_library(LIBRARY_ROOT, load_sources()):
        print("\n".join(problems), file=sys.stderr)
        raise SystemExit(1)
    if LIBRARY_ROOT.is_dir():
        load_library_from(LIBRARY_ROOT)
    print("skill library matches sources.toml")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="pin and sync upstream skills")
    add.add_argument("repo", help="GitHub OWNER/REPO")
    add.add_argument("paths", nargs="+", metavar="PATH", help="skill directories")
    add.add_argument("--group", help="platform the skills work with, for a new source")
    add.add_argument(
        "--summary", help="one-line list summary for a new group or standalone skill"
    )
    add.add_argument("--description", help="what a new group is and why it exists")
    add.add_argument("--exclude", action="append", default=[], metavar="GLOB")
    add.add_argument("--commit", help="pin this commit instead of the latest")
    add.add_argument("--license", dest="license_id", help="SPDX identifier")
    for name, help_text in (
        ("sync", "rewrite skill directories from their pinned commits"),
        ("update", "pin the latest upstream commits and sync"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("keys", nargs="*", metavar="GROUP_OR_REPO")
    commands.add_parser("check", help="fail if directories differ from sources.toml")

    args = parser.parse_args(argv)
    match args.command:
        case "add":
            cmd_add(
                args.repo,
                args.paths,
                args.group,
                args.exclude,
                args.commit,
                args.license_id,
                args.description,
                args.summary,
            )
        case "sync":
            cmd_sync(args.keys)
        case "update":
            cmd_update(args.keys)
        case "check":
            cmd_check()


if __name__ == "__main__":
    main()
