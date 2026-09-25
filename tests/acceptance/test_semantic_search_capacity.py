"""Opt-in controlled capacity run; this does not measure semantic relevance."""

import json
import os
import platform
import resource
from pathlib import Path
from statistics import median
from time import perf_counter

import pytest
import sqlalchemy as sa
from tracecat_registry.core.table import search

from tests.acceptance import test_semantic_search as acceptance
from tracecat.db.models import SearchChunk, SearchDocument
from tracecat.tables.schemas import TableRowInsert

close_redis_after_test = acceptance.close_redis_after_test
encryption_key = acceptance.encryption_key
journey = acceptance.journey
provider_server = acceptance.provider_server
retrieval_case = acceptance.retrieval_case
scoped_database = acceptance.scoped_database
table = acceptance.table
tables = acceptance.tables
workflow_bucket = acceptance.workflow_bucket

ROWS = int(os.getenv("SEMANTIC_SEARCH_CAPACITY_ROWS", "0"))
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(ROWS == 0, reason="opt-in capacity run"),
]


def summary(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "p50_ms": median(values) * 1000,
        "p95_ms": ordered[min(len(values) - 1, int(len(values) * 0.95))] * 1000,
    }


async def test_mixed_rows_and_long_document_capacity(journey):
    assert 1000 <= ROWS <= 10000
    case, table, ready = journey
    tables = case.tables
    probe = await tables.insert_row(
        table, TableRowInsert(data={"key": "probe", "body": "probe", "count": 0})
    )

    async def probe_latency(reads, writes):
        start = perf_counter()
        await tables.get_row(table, probe["id"])
        reads.append(perf_counter() - start)
        start = perf_counter()
        await tables.update_row(table, probe["id"], {"count": len(writes)})
        writes.append(perf_counter() - start)

    baseline_read, baseline_write, active_read, active_write = [], [], [], []
    for _ in range(20):
        await probe_latency(baseline_read, baseline_write)
    # Around 1,200 chunks at the default 800-token / 128-overlap settings.
    long = await tables.insert_row(
        table,
        TableRowInsert(
            data={
                "key": "long",
                "body": "Ordinary inventory maintenance. " * 160000
                + "stolen credentials",
                "count": 0,
            }
        ),
    )
    await tables.batch_insert_rows(
        table,
        [
            {
                "key": f"synthetic_{i}",
                "body": "Office supplies. " * (1 + i % 30),
                "count": 0,
            }
            for i in range(ROWS - 2)
        ],
    )
    start = perf_counter()

    async def observe():
        await probe_latency(active_read, active_write)

    status = await ready(max_turns=ROWS * 2, observe=observe)
    elapsed = perf_counter() - start
    assert status.index.ready == ROWS
    latencies = []
    for _ in range(20):
        start = perf_counter()
        page = await search(table=case.name, query="account takeover", limit=10)
        latencies.append(perf_counter() - start)
        assert page["items"][0]["row_id"] == str(long["id"])
        assert len({i["row_id"] for i in page["items"]}) == 10
        assert page["capped"]
    count, bytes_ = (
        await tables.session.execute(
            sa.select(
                sa.func.count(),
                sa.func.sum(sa.func.pg_column_size(SearchChunk.embedding)),
            )
            .join(SearchDocument, SearchChunk.document_id == SearchDocument.id)
            .where(SearchDocument.collection_id == case.collection.id)
        )
    ).one()
    report = {
        "mode": "controlled_http_not_real_model",
        "rows": ROWS,
        "model": case.config.spec.model,
        "dimensions": case.config.spec.dimensions,
        "python": platform.python_version(),
        "index_seconds": elapsed,
        "provider_calls_including_queries": len(case.server.calls),
        "provider_billed_tokens": None,
        "chunks": count,
        "vector_value_bytes": bytes_,
        "process_peak_rss_platform_units": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
        "search": summary(latencies),
        "baseline_read": summary(baseline_read),
        "baseline_write": summary(baseline_write),
        "active_read": summary(active_read),
        "active_write": summary(active_write),
        "limitations": [
            "Immediate dispatch omits production ten-second cadence.",
            "Read/write probes run between turns, not under concurrent load.",
            "Peak RSS covers the whole pytest process, not only the worker.",
            "Vector bytes exclude indexes, table overhead and backups.",
        ],
    }
    output = Path(
        os.environ.get("SEMANTIC_SEARCH_REPORT", "/tmp/semantic-search-capacity.json")
    )
    output.write_text(json.dumps(report, indent=2) + "\n")
