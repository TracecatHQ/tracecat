#!/usr/bin/env python3
"""
Verify ATT&CK tags against the live ATT&CK taxonomy, and prime pySigma's cache.

Why this exists
---------------
ATT&CK is not a stable taxonomy. In v19 the `defense-evasion` tactic was retired and
its techniques were redistributed across `stealth` and `defense-impairment`, and a
number of technique IDs were merged, deprecated or revoked. A tag like
`attack.defense-evasion` looks correct to a human reviewer and to any model trained
before that release, and it is now invalid. Manual verification cannot catch a taxonomy
rename, so this script checks against the real data instead.

It does three things a `sigma check` pass does not:

1. **Taxonomy validation.** Does the tactic, technique, group or software ID exist in
   this release? For a revoked technique it names the successor rather than saying
   "deprecated, revoked, merged, or never existed", which is four situations in one
   message and none of them actionable.
2. **Semantic cross-check.** Does each technique actually belong to a tactic the rule
   declares? A rule tagged `attack.stealth` plus `attack.t1105` is taxonomically clean
   and semantically wrong, because T1105 is command-and-control. `sigma check` says
   nothing about this, and the mapping is sitting right there in the same bundle.
3. **Cache priming.** It points pySigma's on-disk cache at the bundle so `sigma check`'s
   `attacktag` validator works for the rest of the session, including offline.

This script is the guardrail, not the intelligence layer. It cannot tell you that
"Winlogon Shell value modification" is T1547.001 rather than T1547.004 — that rationale
is yours to write at Stage 4. What it can do is refuse a mapping that contradicts itself.

Usage
-----
    # prime the cache and print the current taxonomy version
    python3 attack_check.py            # on Windows: python attack_check.py

    # validate specific tags
    python3 attack_check.py attack.stealth attack.t1574.001 attack.command-and-control

    # validate every attack.* tag in a directory of rules, per file, with cross-check
    python3 attack_check.py --rules ./rules/

    # list valid tactic shortnames, or look up which tactics a technique belongs to
    python3 attack_check.py --tactics
    python3 attack_check.py --lookup T1055

    # use a local STIX bundle instead of fetching (air-gapped environments)
    python3 attack_check.py --bundle /path/to/enterprise-attack.json

Exit codes, which are the contract Stage 6 relies on
----------------------------------------------------
    0  every tag was checked and every tag is valid. Unverified semantic mappings may
       still be present; they are printed with an [UNV] prefix and counted in the
       summary, and the delivery must name them.
    1  every tag was checked and at least one is invalid or semantically mis-paired.
    2  the check could not be completed, so NOTHING was verified. Causes: the ATT&CK
       data could not be loaded, the --bundle path could not be read, a rule file could
       not be read or would not parse as YAML, or --rules matched no files at all.
    3  an unexpected error. Also means nothing was verified.

Only exit 1 means "checked, and some are wrong". Exit 2 and 3 mean the tags were never
checked, and saying otherwise in a delivery is the exact false-validation claim this
skill exists to prevent.
"""

import argparse
import glob
import json
import os
import re
import sys
import tempfile
import urllib.request

# pySigma's default source is github.com/.../raw/..., which is blocked by some egress
# policies while raw.githubusercontent.com serves the identical file. Both are tried in
# order, and every failure is reported rather than only the last one — a truncated
# download from the first host looks nothing like a 403 from the second, and reporting
# only the second sends the analyst to fix the wrong thing.
SOURCES = [
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    "enterprise-attack/enterprise-attack.json",
    "https://github.com/mitre-attack/attack-stix-data/raw/refs/heads/master/"
    "enterprise-attack/enterprise-attack.json",
]

# Applied only as a fallback when a rule file will not parse as YAML. The lookbehind
# stops it matching `attack.mitre.org` inside a references: URL, which is otherwise a
# 17% false positive rate in exactly the place this script tells you to trust it.
TAG_RE = re.compile(r"(?<![\w./])attack\.([a-z0-9\-\.]+)", re.IGNORECASE)

# pySigma normally lives in sigma-cli's own virtualenv rather than on the system
# interpreter, so priming its cache from a plain `python3` silently does nothing.
# Re-exec under an interpreter that can import it. Windows pipx layouts use
# Scripts\python.exe, not bin/python, so a POSIX-only list never fires there and the
# cache priming quietly does not happen.
VENV_CANDIDATES = [
    os.path.expanduser("~/.local/share/pipx/venvs/sigma-cli/bin/python"),
    os.path.expanduser("~/.local/pipx/venvs/sigma-cli/bin/python"),
    "/usr/local/pipx/venvs/sigma-cli/bin/python",
    os.path.expanduser(r"~\pipx\venvs\sigma-cli\Scripts\python.exe"),
    os.path.expanduser(r"~\AppData\Local\pipx\pipx\venvs\sigma-cli\Scripts\python.exe"),
    os.path.expanduser(r"~\.local\pipx\venvs\sigma-cli\Scripts\python.exe"),
]


class DataUnavailable(Exception):
    """The ATT&CK data could not be loaded. Nothing was checked."""


class RulesUnreadable(Exception):
    """One or more rule files could not be read or parsed. The check is incomplete."""


class UnparseableRule(Exception):
    """A rule file is not valid YAML, so its tags cannot be trusted."""


def cache_path():
    """Where the bundle is cached.

    `/tmp` is not a path on Windows. Hardcoding it resolves to C:\\tmp under native
    Windows Python, which does not exist on a clean box, so the write raises
    FileNotFoundError and a bare `except Exception` around the fetch blames the network
    for a filesystem problem.
    """
    base = os.environ.get("ATTACK_CACHE_DIR") or tempfile.gettempdir()
    return os.path.join(base, "enterprise-attack.json")


def reexec_under_pysigma():
    try:
        import sigma  # noqa: F401

        return
    except ImportError:
        pass
    if os.environ.get("_ATTACK_CHECK_REEXEC"):
        return
    for candidate in VENV_CANDIDATES:
        if os.path.exists(candidate):
            os.environ["_ATTACK_CHECK_REEXEC"] = "1"
            os.execv(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]])


def load_bundle(path=None):
    """Return (bundle, source_description). Raises DataUnavailable."""
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh), path
        except (OSError, UnicodeDecodeError) as exc:
            raise DataUnavailable(
                f"--bundle path could not be read: {path}\n  {type(exc).__name__}: {exc}\n"
                "This is a filesystem problem, not a network problem. Check the path."
            ) from exc
        except json.JSONDecodeError as exc:
            raise DataUnavailable(
                f"--bundle file is not valid JSON: {path}\n  {exc}"
            ) from exc

    cache = cache_path()
    if os.path.exists(cache) and os.path.getsize(cache) > 1_000_000:
        try:
            with open(cache, "r", encoding="utf-8") as fh:
                return json.load(fh), cache
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass  # a corrupt cache is not fatal, re-fetch below

    # Separate the fetch failure from the write failure, so the message names the real
    # cause, and keep every source's failure rather than only the last.
    failures = []
    for url in SOURCES:
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"  fetch  {url}\n         {type(exc).__name__}: {exc}")
            continue
        try:
            with open(cache, "wb") as fh:
                fh.write(data)
            written = cache
        except OSError as exc:
            written = None
            failures.append(
                f"  write  {cache}\n         {type(exc).__name__}: {exc}\n"
                "         The download succeeded; caching it did not. Set ATTACK_CACHE_DIR "
                "to a writable directory."
            )
        try:
            bundle = json.loads(data)
        except json.JSONDecodeError as exc:
            failures.append(f"  parse  {url}\n         {exc}")
            continue
        if written is None:
            # Usable this run, but `sigma check` cannot be primed from a file that does
            # not exist, so say so rather than letting the caller assume it was.
            print(
                "WARNING: bundle fetched but could not be cached, so the pySigma "
                "attacktag validator cannot be primed from disk. Details:\n"
                + "\n".join(failures),
                file=sys.stderr,
            )
        return bundle, url

    raise DataUnavailable(
        "Could not load ATT&CK data. Every source failed:\n"
        + "\n".join(failures)
        + "\n\nPass --bundle /path/to/enterprise-attack.json, or state in the delivery "
        "that ATT&CK tags could not be machine-verified."
    )


def parse(bundle):
    """Return the pieces needed for taxonomy and semantic checks.

    `revoked_by` maps a revoked ATT&CK id to its successor. The successor is already in
    this bundle: the relationship objects carry it, and the previous version of this
    script walked straight past every object of type `relationship`.
    """
    version = None
    tactics, techniques = {}, {}
    groups, software = set(), set()
    stix_to_attack, attack_to_name = {}, {}
    revoked_rels = {}

    for obj in bundle.get("objects", []):
        kind = obj.get("type")

        if kind == "relationship":
            if obj.get("relationship_type") == "revoked-by":
                revoked_rels[obj.get("source_ref")] = obj.get("target_ref")
            continue

        ext = None
        for ref in obj.get("external_references", []):
            if ref.get("source_name") == "mitre-attack":
                ext = ref.get("external_id")

        # Index every attack-pattern, revoked ones included, so a successor can be named.
        if kind == "attack-pattern" and ext:
            stix_to_attack[obj["id"]] = ext
            attack_to_name[ext] = obj.get("name", "")

        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue

        if kind == "x-mitre-collection":
            version = obj.get("x_mitre_version")
        elif kind == "x-mitre-tactic":
            tactics[obj["x_mitre_shortname"]] = obj.get("name", "")
        elif kind == "attack-pattern" and ext:
            techniques[ext.lower()] = (
                obj.get("name", ""),
                [
                    p["phase_name"]
                    for p in obj.get("kill_chain_phases", [])
                    if p.get("kill_chain_name") == "mitre-attack"
                ],
            )
        elif kind == "intrusion-set" and ext:
            groups.add(ext.lower())
        elif kind in ("malware", "tool") and ext:
            software.add(ext.lower())

    revoked_by = {}
    for src, dst in revoked_rels.items():
        old, new = stix_to_attack.get(src), stix_to_attack.get(dst)
        if old and new:
            revoked_by[old.lower()] = (new, attack_to_name.get(new, ""))

    return version, tactics, techniques, groups, software, revoked_by


def check(tag, tactics, techniques, groups, software, revoked_by):
    """Return (state, message) for one bare tag value, e.g. 't1055' or 'stealth'.

    state is one of VALID, INVALID, UNVERIFIED.
    """
    val = tag.lower()
    if val in tactics:
        return "VALID", f"tactic '{val}' ({tactics[val]})"
    if val.startswith("t") and val[1:2].isdigit():
        hit = techniques.get(val)
        if hit:
            if not hit[1]:
                return "UNVERIFIED", (
                    f"technique {val.upper()} '{hit[0]}' exists but declares no ATT&CK "
                    "tactic, so the mapping cannot be cross-checked"
                )
            return "VALID", f"technique {val.upper()} '{hit[0]}' -> tactics {hit[1]}"
        if val in revoked_by:
            new, name = revoked_by[val]
            return "INVALID", (
                f"technique {val.upper()} was REVOKED, superseded by {new} '{name}'. "
                f"Retag as attack.{new.lower()} and confirm the tactic still fits."
            )
        return "INVALID", (
            f"technique {val.upper()} is not present in this ATT&CK release, and no "
            "revoked-by successor is recorded for it, so it was deprecated or never existed"
        )
    if val.startswith("g") and val[1:2].isdigit():
        return ("VALID" if val in groups else "INVALID"), f"group {val.upper()}"
    if val.startswith("s") and val[1:2].isdigit():
        return ("VALID" if val in software else "INVALID"), f"software {val.upper()}"
    if val.startswith("ds") and val[2:3].isdigit():
        return "INVALID", (
            f"{val.upper()} is an ATT&CK data-source id. pySigma's attacktag validator "
            "does not accept the ds namespace at all, so `sigma check` raises "
            "InvalidATTACKTagIssue on it regardless of whether the data source exists. "
            "Drop the tag; there is no valid form of it."
        )
    return "INVALID", (
        f"'{val}' is not a valid tactic shortname. Valid tactics: " + ", ".join(sorted(tactics))
    )


def cross_check(declared_tactics, declared_techniques, techniques):
    """Technique-to-tactic semantic cross-check for one rule.

    Returns a list of (state, message). A technique whose ATT&CK tactics share nothing
    with the tactics the rule declares is BAD: the taxonomy is fine and the mapping is
    not. Where the rule declares no tactic at all there is nothing to check against, so
    the result is UNVERIFIED rather than a pass.
    """
    out = []
    if not declared_techniques:
        return out
    if not declared_tactics:
        return [
            (
                "UNVERIFIED",
                "rule declares techniques but no tactic, so the technique-to-tactic "
                "mapping could not be cross-checked",
            )
        ]
    for tech in declared_techniques:
        hit = techniques.get(tech)
        if not hit or not hit[1]:
            continue  # already reported by check()
        if not (set(hit[1]) & set(declared_tactics)):
            out.append(
                (
                    "INVALID",
                    f"{tech.upper()} requires one of {sorted(hit[1])}, "
                    f"rule declares {sorted(declared_tactics)}",
                )
            )
    return out


def prime_pysigma(path):
    """Point pySigma's validator at the local bundle so `sigma check` works offline.

    Returns (ok, reason). Stage 6 depends on the priming having worked, so a bare False with
    no cause sends the analyst looking in the wrong place: not-importable and
    importable-but-rejected-the-bundle need different fixes.
    """
    try:
        from sigma.data import mitre_attack
    except ImportError as exc:
        return False, (
            f"pySigma is not importable from this interpreter ({exc}). `sigma check` will "
            "re-fetch the ATT&CK data itself and will fail if egress is blocked. Run this "
            "script with sigma-cli's own interpreter, or set ATTACK_CACHE_DIR."
        )
    try:
        mitre_attack.set_url(path)
        _ = mitre_attack.mitre_attack_tactics
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def tags_from_file(path):
    """Return the attack.* tag values declared in one rule file.

    Read the YAML and take `tags:` directly rather than regexing raw file text. PyYAML
    is already a transitive dependency of pySigma, and the regex approach matches
    `attack.mitre.org` in the references block — against a 411-rule corpus that was 16
    of 94 flagged tags, a 17% false positive rate in exactly the place this script tells
    the model to trust the tool completely. A model that obediently "fixes" those starts
    deleting reference URLs.
    """
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import yaml

        found = []
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict):
                for tag in doc.get("tags") or []:
                    if isinstance(tag, str) and tag.lower().startswith("attack."):
                        found.append(tag.split(".", 1)[1])
        return found
    except ImportError:
        # PyYAML absent. Fall back to the tightened regex; the tags are still checked.
        return [m.group(1) for m in TAG_RE.finditer(text)]
    except Exception as exc:  # noqa: BLE001
        # The file will not parse as YAML, so `sigma check` will fail on it too, and any tags
        # a regex scrapes out of a broken document are not trustworthy. Degrading quietly to a
        # regex here would let a malformed rule report "0 valid, 0 invalid" and exit 0, which
        # is the silent success this script exists to refuse.
        raise UnparseableRule(f"{path}: {type(exc).__name__}: {exc}") from exc


def collect_rules(target):
    files = (
        sorted(glob.glob(os.path.join(target, "**", "*.yml"), recursive=True))
        if os.path.isdir(target)
        else sorted(glob.glob(target))
    )
    if not files:
        raise RulesUnreadable(
            f"--rules {target!r} matched no .yml files. Nothing was checked.\n"
            "  Check the path, and note that only *.yml is scanned, not *.yaml."
        )
    per_file, unreadable = {}, []
    for path in files:
        try:
            per_file[path] = tags_from_file(path)
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append((path, f"{type(exc).__name__}: {exc}"))
        except UnparseableRule as exc:
            unreadable.append((path, f"not valid YAML - {exc}"))
    return per_file, unreadable


def run(args):
    bundle, source = load_bundle(args.bundle)
    version, tactics, techniques, groups, software, revoked_by = parse(bundle)
    local = args.bundle or cache_path()
    if os.path.exists(local):
        primed, prime_reason = prime_pysigma(local)
    else:
        primed, prime_reason = False, f"no local bundle at {local}"


    print(f"ATT&CK Enterprise v{version}  (source: {source})")
    print(f"{len(techniques)} live techniques, {len(tactics)} tactics, "
          f"{len(revoked_by)} revoked techniques with a recorded successor")
    print(f"pySigma attacktag validator primed: {primed}")
    if not primed:
        print(f"  reason: {prime_reason}")
        print("  `sigma check` will need its own ATT&CK egress, or -x attacktag.")

    if args.tactics:
        for short, name in sorted(tactics.items()):
            print(f"  {short:<24} {name}")
        return 0

    if args.lookup:
        key = "t" + args.lookup.lower().lstrip("tT")
        hit = techniques.get(key)
        if hit:
            print(f"  {args.lookup.upper()}: '{hit[0]}' -> tactics {hit[1]}")
            return 0
        if key in revoked_by:
            new, name = revoked_by[key]
            print(f"  {args.lookup.upper()}: REVOKED, superseded by {new} '{name}'")
            return 1
        print(f"  {args.lookup.upper()}: NOT PRESENT in this release")
        return 1

    # Group by file so the semantic cross-check has a rule to reason about. Flattening
    # the tags is what let `attack.stealth` plus `attack.t1105` pass: each tag is fine
    # in isolation and the pair is not.
    per_file = {}
    unreadable = []
    if args.tags:
        per_file["(command line)"] = [
            t.split(".", 1)[1] if t.lower().startswith("attack.") else t for t in args.tags
        ]
    if args.rules:
        found, unreadable = collect_rules(args.rules)
        per_file.update(found)

    if unreadable:
        detail = "\n".join(f"  [ERR] {p}: {e}" for p, e in unreadable)
        raise RulesUnreadable(
            f"{len(unreadable)} rule file(s) could not be read or parsed, so their tags were "
            f"never checked:\n{detail}"
        )

    if not per_file:
        print("\nNo tags given. Cache is primed; `sigma check` can now run attacktag.")
        return 0

    print()
    counts = {"VALID": 0, "INVALID": 0, "UNVERIFIED": 0}
    prefix_for = {"VALID": "OK ", "INVALID": "BAD", "UNVERIFIED": "UNV"}
    for path, raw_tags in sorted(per_file.items()):
        label = os.path.basename(path) if path != "(command line)" else ""
        declared_tactics, declared_techniques = [], []
        for tag in raw_tags:
            state, msg = check(tag, tactics, techniques, groups, software, revoked_by)
            counts[state] += 1
            head = f"{label}: " if label else ""
            print(f"  [{prefix_for[state]}] {head}attack.{tag} - {msg}")
            low = tag.lower()
            if low in tactics:
                declared_tactics.append(low)
            elif low.startswith("t") and low[1:2].isdigit():
                declared_techniques.append(low)
        if path == "(command line)":
            continue  # a loose list of tags is not a rule, so there is nothing to cross-check
        for state, msg in cross_check(declared_tactics, declared_techniques, techniques):
            counts[state] += 1
            head = f"{label}: " if label else ""
            print(f"  [{prefix_for[state]}] {head}{msg}")

    print(
        f"\n{counts['VALID']} valid, {counts['INVALID']} invalid, "
        f"{counts['UNVERIFIED']} unverified"
    )
    if counts["UNVERIFIED"]:
        print(
            "UNVERIFIED means the technique exists but its mapping to the behaviour could "
            "not be confirmed here. Name these in the delivery; do not round them to valid."
        )
    return 1 if counts["INVALID"] else 0


def main():
    # The per-result lines and the summary both carry non-ASCII characters, and a cp1252
    # Windows console raises UnicodeEncodeError on them. It fires on the *valid* path, so
    # the cleaner the rules the sooner it dies: against a 420-tag corpus the previous
    # version printed six result lines and stopped. Reconfigure the stream rather than
    # substituting characters, because there is always another one.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("tags", nargs="*", help="tags to check, e.g. attack.t1055")
    ap.add_argument("--rules", help="directory or glob of .yml rules to scan for tags")
    ap.add_argument("--bundle", help="path to a local enterprise-attack.json")
    ap.add_argument("--tactics", action="store_true", help="list valid tactic shortnames")
    ap.add_argument("--lookup", help="look up one technique id, e.g. T1055")
    args = ap.parse_args()

    # reexec is inside the guard on purpose. os.execv can raise PermissionError on a pipx
    # candidate that exists but is not executable, and an unguarded traceback there exits 1,
    # which the contract above reserves for "checked, and some are invalid". Nothing was
    # checked, so it has to come out as 3.
    try:
        reexec_under_pysigma()
        return run(args)
    except (DataUnavailable, RulesUnreadable) as exc:
        print(f"\n{exc}", file=sys.stderr)
        print(
            "\nExit 2: the check did not complete, so NO tags were verified. Say that in "
            "the delivery rather than describing the tags as checked.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(
            f"\nExit 3: unexpected error ({type(exc).__name__}), so NO tags were verified. "
            "Say that in the delivery rather than describing the tags as checked.",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    sys.exit(main())
