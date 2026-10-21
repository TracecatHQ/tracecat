#!/usr/bin/env python3
"""
Catch converted queries whose operator grouping does not match the rule's intent.

Why this exists
---------------
pySigma's Splunk backend declares operator precedence as NOT, OR, AND. SPL binds the
other way round: NOT, then AND, then OR. When a search identifier holds a list of maps
(an OR of ANDs) and the condition ANDs it with anything else, the backend emits the OR
group without an enclosing bracket:

    EventID=1 (ParentImage="*\\svchost.exe" ParentCommandLine="*-s Schedule*")
    OR ParentImage="*\\taskeng.exe" Image="*\\AppData\\Local\\*" NOT Image="*\\OneDriveSetup.exe"

Splunk reads that as (EventID=1 AND svchost AND schedule) OR (taskeng AND path AND NOT
filter). The first branch has lost the path restriction and every filter, so a narrow
rule becomes one that fires on everything the anchor matches.

`sigma check` passes this and `sigma convert` succeeds, so nothing in the normal
validation loop catches it. The inner brackets ARE emitted, which is what makes it easy
to skim past: the missing bracket is the outer one.

This script converts each rule and fails on any boolean operator sitting at bracket
depth zero alongside other terms, which is the signature of the defect.

Usage
-----
    python3 check_grouping.py --backend splunk --pipeline sysmon rules/
    python3 check_grouping.py -t lucene --without-pipeline rules/   # logsource has no pipeline
    python3 check_grouping.py -t splunk -p sysmon -p splunk_windows rules/one_rule.yml
    python3 check_grouping.py -t kusto -p microsoft_xdr rules/      # should pass cleanly

Exit codes: 0 no grouping risk found, 1 at least one query is ambiguous, 2 conversion
failed or sigma is not installed.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys

# Backends whose emitted precedence is known to disagree with the target query language.
# Others are checked too, but a hit on these is treated as a defect rather than a warning.
KNOWN_UNSAFE = {"splunk", "splunk_spl2"}


def find_sigma():
    exe = shutil.which("sigma")
    if exe:
        return exe
    for candidate in (
        os.path.expanduser("~/.local/bin/sigma"),
        "/usr/local/bin/sigma",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def top_level_operators(query):
    """
    Return the set of boolean operators appearing at bracket depth 0, ignoring anything
    inside quotes. An unbracketed OR next to other terms is the failure signature.
    """
    depth = 0
    quote = None
    token = ""
    found = set()
    terms_at_top = 0
    i = 0
    while i < len(query):
        ch = query[i]
        if quote:
            if ch == quote and query[i - 1 : i] != "\\":
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch in "([":
            depth += 1
            if depth == 1:
                terms_at_top += 1
            token = ""
            i += 1
            continue
        if ch in ")]":
            depth -= 1
            token = ""
            i += 1
            continue
        if depth == 0:
            if ch.isspace():
                word = token.strip().upper()
                if word in ("OR", "AND", "NOT"):
                    found.add(word)
                elif word:
                    terms_at_top += 1
                token = ""
            else:
                token += ch
        i += 1
    word = token.strip().upper()
    if word in ("OR", "AND", "NOT"):
        found.add(word)
    elif word:
        terms_at_top += 1
    return found, terms_at_top


def analyse(query, backend):
    """Return (verdict, explanation). verdict is 'ok', 'risk', or 'defect'."""
    ops, terms = top_level_operators(query)
    if "OR" not in ops:
        return "ok", "no unbracketed OR at the top level"
    # An OR at the top level is only dangerous when something else is ANDed with it,
    # which is what lets the OR swallow the neighbouring terms.
    if terms <= 1 and ops == {"OR"}:
        return "ok", "top level is a single OR list, unambiguous"
    verdict = "defect" if backend in KNOWN_UNSAFE else "risk"
    return verdict, (
        "an OR sits at the outermost level next to other terms. In a query language "
        "where AND binds tighter than OR, the OR splits the whole query into two "
        "branches and each branch keeps only the terms adjacent to it, so filters and "
        "restrictions on one side silently stop applying"
    )


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("rules", nargs="+", help="rule files, directories, or globs")
    ap.add_argument("-t", "--backend", required=True)
    ap.add_argument("-p", "--pipeline", action="append", default=[])
    # Passed straight through to `sigma convert`. Without it this script cannot run at all on
    # a logsource-and-backend pair that has no pipeline — Lucene refuses to convert with no
    # pipeline and names this flag, so a Linux rule on Elastic made the mandated Stage 6
    # grouping check unrunnable. The grouping logic below is untouched.
    ap.add_argument(
        "--without-pipeline",
        action="store_true",
        help="convert with --without-pipeline, for a logsource with no pipeline on this backend",
    )
    args = ap.parse_args()
    if args.pipeline and args.without_pipeline:
        print("Pass either -p or --without-pipeline, not both.", file=sys.stderr)
        return 2

    sigma = find_sigma()
    if not sigma:
        print("sigma-cli not found. Install with `pipx install sigma-cli`.")
        return 2

    files = []
    for target in args.rules:
        if os.path.isdir(target):
            files += sorted(glob.glob(os.path.join(target, "**", "*.yml"), recursive=True))
        else:
            files += sorted(glob.glob(target))
    if not files:
        print("No .yml rules found.")
        return 2

    pipe_desc = args.pipeline or (["--without-pipeline"] if args.without_pipeline else ["none"])
    print(f"Grouping check: backend={args.backend} pipeline={pipe_desc}")
    print(f"{len(files)} rule(s)\n")

    failed = 0
    for path in files:
        cmd = [sigma, "convert", "-t", args.backend]
        for pipe in args.pipeline:
            cmd += ["-p", pipe]
        if args.without_pipeline:
            cmd.append("--without-pipeline")
        cmd.append(path)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"  [ERR] {os.path.basename(path)} — conversion failed")
            print(f"        {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}")
            failed += 1
            continue
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip() and "Parsing Sigma" not in ln]
        query = " ".join(lines)
        verdict, why = analyse(query, args.backend)
        label = {"ok": "OK ", "risk": "WARN", "defect": "BAD"}[verdict]
        print(f"  [{label}] {os.path.basename(path)} — {why}")
        if verdict != "ok":
            print(f"        {query[:300]}{'...' if len(query) > 300 else ''}")
            print(
                "        Fix: restructure the selection so the top level is pure AND, "
                "split the OR branch into its own rule, or hand-bracket the deployed "
                "query and record that you did."
            )
            failed += 1

    print(f"\n{len(files) - failed} clean, {failed} needing attention")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
