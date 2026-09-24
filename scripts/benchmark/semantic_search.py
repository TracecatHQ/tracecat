"""Opt-in relevance evaluation through an existing development workspace.

Uses real configured providers and the deployed indexing worker. Creates only a
synthetic table and leaves it for browser inspection. No credentials enter reports.
Run with --help; provider calls require --allow-provider-calls explicitly.
"""

import argparse
import asyncio
import json
import platform
import subprocess
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from tracecat.auth.types import Role
from tracecat.authz.scopes import ORG_ADMIN_SCOPES
from tracecat.search.embeddings.service import resolve_embedding_configuration
from tracecat.search.retrieval import TableRetrievalService
from tracecat.search.schemas import SearchRequest
from tracecat.search.types import SearchScope
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.search.schemas import TableSearchDisplayState, TableSearchSelection
from tracecat.tables.service import TablesService

# Labels and queries are fixed before running a model; rankings cannot change
# the expected answers. All content is synthetic.
CASES = (
    (
        "takeover",
        "Account access",
        "An intruder signed in using stolen credentials.",
        "Someone took over an account",
    ),
    (
        "exfiltration",
        "Unexpected transfer",
        "Sensitive documents were copied from the server to an external destination.",
        "Data was stolen from our network",
    ),
    (
        "phishing",
        "Deceptive message",
        "An email impersonated the help desk and requested a password on a fake login page.",
        "An attacker tricked a user into revealing their password",
    ),
    (
        "disk",
        "Storage incident",
        "The filesystem ran out of free space and could no longer accept writes.",
        "The disk is full",
    ),
    (
        "identifier",
        "Synthetic indicator",
        "Investigation marker SYNTHETIC-4821 belongs to this record.",
        "SYNTHETIC-4821",
    ),
)


def corpus(rows: int):
    for label, title, passage, _ in CASES:
        yield label, {"title": title, "description": passage, "status": "open"}
        yield (
            label,
            {
                "title": title + " detailed",
                "description": "Routine inventory inspection. " * 1200 + passage,
                "status": "open",
            },
        )
    for i in range(rows - len(CASES) * 2):
        yield (
            "irrelevant",
            {
                "title": f"Synthetic office record {i}",
                "description": "Office supplies were restocked. " * (1 + i % 30),
                "status": "closed",
            },
        )


async def run(args):
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=args.organization_id,
        workspace_id=args.workspace_id,
        scopes=ORG_ADMIN_SCOPES,
    )
    scope = SearchScope(args.organization_id, args.workspace_id)
    pinned = await resolve_embedding_configuration(scope)
    if pinned is None:
        raise RuntimeError("No eligible provider in the selected development workspace")
    # Do not write any credential IDs, endpoints or tenant IDs to the report.
    model = asdict(pinned.spec)
    model.pop("endpoint")
    table_name = "semantic_eval_" + uuid4().hex[:12]
    labels = {}
    async with TablesService.with_session(role=role) as tables:
        table = await tables.create_table(
            TableCreate(
                name=table_name,
                columns=[
                    TableColumnCreate(name="title", type=SqlType.TEXT),
                    TableColumnCreate(name="description", type=SqlType.TEXT),
                    TableColumnCreate(name="status", type=SqlType.TEXT),
                ],
            )
        )
        for label, row in corpus(args.rows):
            inserted = await tables.insert_row(table, TableRowInsert(data=row))
            labels[inserted["id"]] = (
                label,
                "long" if len(row["description"]) > 1000 else "short",
            )
        generation = 0
        for name in ("title", "description"):
            selected = await tables.search.select_column(
                table.id,
                TableSearchSelection(
                    column_id=next(c.id for c in table.columns if c.name == name),
                    enabled=True,
                    expected_generation=generation,
                ),
            )
            assert selected is not None
            generation = selected.generation
            await tables.session.commit()
        print(
            f"Created {table_name}; waiting for the deployed worker to reach Ready",
            flush=True,
        )
        start = perf_counter()
        while perf_counter() - start < args.timeout:
            await tables.session.rollback()
            status = await tables.search.configuration(table.id)
            await tables.session.commit()
            if status.status == TableSearchDisplayState.READY:
                break
            if status.status == TableSearchDisplayState.NEEDS_ATTENTION:
                raise RuntimeError("Index needs attention; inspect typed table errors")
            await asyncio.sleep(2)
        else:
            raise TimeoutError("Index did not reach Ready within the evaluation budget")
        indexing_seconds = perf_counter() - start
    service = TableRetrievalService(role)
    evaluations = []
    for label, _, _, query in CASES:
        start = perf_counter()
        page = await service.search(
            table_name, SearchRequest(query=query, limit=args.k)
        )
        elapsed = perf_counter() - start
        relevant = sum(labels[item.row_id][0] == label for item in page.items)
        evaluations.append(
            {
                "case": label,
                "hit_at_k": relevant > 0,
                "recall_at_k": relevant / 2,
                "nonrelevant_at_k": len(page.items) - relevant,
                "latency_ms": elapsed * 1000,
                "ranked_labels": [labels[item.row_id][0] for item in page.items],
                "relevant_ranks": {
                    variant: next(
                        (
                            rank
                            for rank, item in enumerate(page.items, 1)
                            if labels[item.row_id] == (label, variant)
                        ),
                        None,
                    )
                    for variant in ("short", "long")
                },
            }
        )
    report = {
        "mode": "real_configured_provider",
        "model": model,
        "recipe_revision": pinned.recipe_revision,
        "configuration_version": pinned.version,
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "python": platform.python_version(),
        "rows": args.rows,
        "k": args.k,
        "indexing_seconds": indexing_seconds,
        "evaluations": evaluations,
        "hit_rate_at_k": sum(e["hit_at_k"] for e in evaluations) / len(evaluations),
        "limitations": [
            "Small synthetic relevance set, not a general quality guarantee.",
            "Provider billing and worker memory must be captured separately.",
            "No score threshold: nearest results can be irrelevant.",
        ],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Report: {args.output}. Synthetic table retained for UI/workflow QA: {table_name}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument(
        "--allow-provider-calls",
        action="store_true",
        help="Opt in to real provider processing and usage charges",
    )
    parser.add_argument("--rows", type=int, default=20)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/semantic-search-relevance.json")
    )
    args = parser.parse_args()
    if not args.allow_provider_calls:
        parser.error(
            "--allow-provider-calls is required; use a synthetic development workspace"
        )
    if not 10 <= args.rows <= 10000 or not 1 <= args.k <= 100 or args.timeout <= 0:
        parser.error("rows must be 10–10000, k 1–100, and timeout positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
